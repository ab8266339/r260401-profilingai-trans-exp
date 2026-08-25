/**
 * mmas-ul-validation.cc  —  ns-3.35 simulation
 * mMAS Uplink Queuing Validation (M/G/1 model, Corollary 1)
 *
 * Topology:
 *   N UE nodes  ──(1 Gbps P2P)──> gNB/router ──(BOTTLENECK 10 Mbps P2P)──> MEC
 *
 * The 10 Mbps bottleneck link models the effective LTE UL capacity
 * (50 Mbps raw × TDD ratio 0.20 = 10 Mbps).  A FifoQueueDisc is
 * placed on the bottleneck to enforce M/G/1-style FCFS queuing.
 *
 * Traffic model (Table III of paper):
 *   Each UE sends Poisson arrivals (λ = 0.2 req/s) via OnOffApplication.
 *   Packet size S = E[s_sense/κ] + β·E[τ_in]
 *                 = 25 000 + 4 × 457 = 26 828 bytes
 *   (mean of Uniform[0,50000] + 4 × lognormal(5,1.5) capped at 2048)
 *
 * M/D/1 reference (deterministic service, lower bound on M/G/1):
 *   ρ = N·λ·E[S] / C_UL
 *   W = E[S]/C_UL + ρ·E[S] / (2·C_UL·(1-ρ))    [P-K formula, M/D/1]
 *
 * Output (stdout, one line):
 *   RESULT N=<n> sim_ms=<mean UL sojourn> theory_ms=<M/D/1> rho=<util>
 *
 * Compile:
 *   See build.sh
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/point-to-point-net-device.h"
#include <iomanip>

#include <random>
#include <algorithm>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("MmasUlValidation");

// M/D/1 Pollaczek–Khinchine sojourn time (ms)
static double MD1Sojourn(double N, double lambda, uint32_t pktBytes, double cUlBps)
{
    double ES  = pktBytes * 8.0 / cUlBps;          // mean service time (s)
    double rho = N * lambda * ES;
    if (rho >= 0.999) return 1e6;
    // M/D/1 P-K: Wq = rho^2 / (2·lambda_total·(1-rho)) — wait only
    double lambdaTotal = N * lambda;
    double Wq = (lambdaTotal * ES * ES) / (2.0 * (1.0 - rho));  // M/D/1
    return (Wq + ES) * 1000.0;                                    // ms
}

// M/G/1 Pollaczek–Khinchine sojourn time (ms) from payload moments (bytes)
static double MG1Sojourn(double N, double lambda, double ESbytes,
                         double ES2bytes2, double cUlBps)
{
    double ES  = ESbytes  * 8.0 / cUlBps;                       // E[S] (s)
    double ES2 = ES2bytes2 * 64.0 / (cUlBps * cUlBps);          // E[S^2] (s^2)
    double rho = N * lambda * ES;
    if (rho >= 0.999) return 1e6;
    double Wq = (N * lambda * ES2) / (2.0 * (1.0 - rho));       // M/G/1 P-K
    return (Wq + ES) * 1000.0;                                   // ms
}

int main(int argc, char *argv[])
{
    // ── Parameters ──────────────────────────────────────────────────────────
    uint32_t nUes      = 50;      // number of agent-UEs
    double   simTime   = 60.0;    // simulation duration (s, post-warmup)
    double   warmup    = 10.0;    // warmup period (s, excluded from stats)
    double   lambda    = 0.2;     // Poisson request rate per UE (Hz)
    uint32_t pktSize   = 26828;   // bytes: mean mMAS UL payload (AFS-B)
    double   bottleMbps = 10.0;   // effective UL capacity (Mbps)
    uint32_t seed      = 1;
    bool     randomPayload = false;  // per-UE payloads from the model mixture

    CommandLine cmd;
    cmd.AddValue("nUes",       "Number of UE agents",             nUes);
    cmd.AddValue("simTime",    "Post-warmup simulation time (s)", simTime);
    cmd.AddValue("lambda",     "Request rate per agent (Hz)",     lambda);
    cmd.AddValue("pktSize",    "UL packet size (bytes)",          pktSize);
    cmd.AddValue("bottleMbps", "Bottleneck link capacity (Mbps)", bottleMbps);
    cmd.AddValue("seed",       "RNG seed",                        seed);
    cmd.AddValue("randomPayload",
                 "Draw per-UE payloads from U(0,50KB) + 4*truncLN(5,1.5,2048)",
                 randomPayload);
    cmd.Parse(argc, argv);

    // Per-UE payload sizes. Fixed mode: all equal to pktSize (M/D/1 setting).
    // Random mode: per-UE draw from the model's sensing+token mixture; with
    // equal per-UE rates the packet-size distribution seen by the bottleneck
    // queue equals the mixture, so M/G/1 P-K with the empirical moments applies.
    std::vector<uint32_t> uePktSize(nUes, pktSize);
    double ESbytes = pktSize, ES2bytes2 = (double)pktSize * pktSize;
    if (randomPayload) {
        std::mt19937_64 gen(1000003ULL * seed + 17);
        std::uniform_real_distribution<double> sense(0.0, 50000.0);
        std::lognormal_distribution<double> tokens(5.0, 1.5);
        double s1 = 0.0, s2 = 0.0;
        for (uint32_t u = 0; u < nUes; u++) {
            double tau = std::min(tokens(gen), 2048.0);   // truncated at tau_max
            double S = sense(gen) + 4.0 * tau;            // bytes
            uePktSize[u] = std::max(200u, (uint32_t)S);
            s1 += uePktSize[u];
            s2 += (double)uePktSize[u] * uePktSize[u];
        }
        ESbytes = s1 / nUes;
        ES2bytes2 = s2 / nUes;
    }

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(seed);

    // ── Topology: N UEs + 1 gNB/router + 1 MEC ──────────────────────────────
    NodeContainer ueNodes;    ueNodes.Create(nUes);
    NodeContainer gnbNode;    gnbNode.Create(1);    // shared router / gNB
    NodeContainer mecNode;    mecNode.Create(1);    // MEC server

    InternetStackHelper stack;
    stack.Install(ueNodes);
    stack.Install(gnbNode);
    stack.Install(mecNode);

    // Access links: UE_i ──(1 Gbps, 0.1 ms)──> gNB
    PointToPointHelper accessLink;
    accessLink.SetDeviceAttribute("DataRate", StringValue("1Gbps"));
    accessLink.SetChannelAttribute("Delay",   StringValue("0.1ms"));

    Ipv4AddressHelper addrHelper;
    std::vector<Ipv4Address> ueAddresses(nUes);
    std::vector<NetDeviceContainer> ueGnbDevs(nUes);

    for (uint32_t u = 0; u < nUes; u++) {
        ueGnbDevs[u] = accessLink.Install(ueNodes.Get(u), gnbNode.Get(0));
        std::ostringstream subnet;
        subnet << "10." << (u / 256) << "." << (u % 256) << ".0";
        addrHelper.SetBase(subnet.str().c_str(), "255.255.255.0");
        addrHelper.Assign(ueGnbDevs[u]);
        ueAddresses[u] = ueGnbDevs[u].Get(0)->GetNode()
                             ->GetObject<Ipv4>()
                             ->GetAddress(1, 0).GetLocal();
    }

    // Bottleneck link: gNB ──(bottleMbps, 0.5 ms)──> MEC
    std::ostringstream bottleRate;
    bottleRate << bottleMbps << "Mbps";
    PointToPointHelper bottleLink;
    bottleLink.SetDeviceAttribute("DataRate", StringValue(bottleRate.str()));
    bottleLink.SetChannelAttribute("Delay",   StringValue("0.5ms"));
    NetDeviceContainer gnbMecDevs = bottleLink.Install(gnbNode.Get(0), mecNode.Get(0));

    addrHelper.SetBase("192.168.0.0", "255.255.255.0");
    Ipv4InterfaceContainer gnbMecIfaces = addrHelper.Assign(gnbMecDevs);
    Ipv4Address mecAddr = gnbMecIfaces.GetAddress(1);

    // Bottleneck uses P2P DropTailQueue (FIFO) by default — no explicit
    // TrafficControlHelper needed; ns-3.35 P2P devices default to FIFO.
    // Set the queue depth large enough to avoid artificial drop.
    Ptr<NetDevice> bottleNetDev = gnbMecDevs.Get(0);
    Ptr<PointToPointNetDevice> p2pDev =
        DynamicCast<PointToPointNetDevice>(bottleNetDev);
    if (p2pDev) {
        p2pDev->GetQueue()->SetMaxSize(QueueSize("10000p"));
    }

    // Global routing
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // ── Applications: UE → MEC (UL Poisson traffic) ─────────────────────────
    uint16_t ulPort = 5001;

    // PacketSink on MEC
    PacketSinkHelper sinkHelper("ns3::UdpSocketFactory",
                                InetSocketAddress(Ipv4Address::GetAny(), ulPort));
    ApplicationContainer sinkApps = sinkHelper.Install(mecNode.Get(0));
    sinkApps.Start(Seconds(0.0));
    sinkApps.Stop(Seconds(warmup + simTime + 1.0));

    // OnOff on each UE: Poisson arrivals via exponential off-time.
    //
    // To send exactly ONE packet per Poisson arrival, the on-time must
    // equal exactly one packet transmission time at the chosen DataRate.
    //   onTime = pktSize * 8 / dataRateBps
    //   At 100 Mbps: 26828 * 8 / 100e6 = 2.146 ms  → 1 packet
    //
    // OffTime ~ Exponential(mean = 1/λ - onTime) → inter-arrival ≈ 1/λ
    double dataRateBps = 100.0e6;    // 100 Mbps on access link
    double onTimeSec   = pktSize * 8.0 / dataRateBps;   // exactly one packet
    double meanOffTime = (1.0 / lambda) - onTimeSec;    // so E[T] = 1/λ
    if (meanOffTime <= 0) meanOffTime = 1.0 / lambda;

    std::ostringstream drStr;
    drStr << (uint64_t)dataRateBps << "bps";

    ApplicationContainer clientApps;
    double stagger = meanOffTime / nUes;   // spread initial offsets
    if (!randomPayload) {
        // Homogeneous path: identical to the original code (single helper,
        // one RNG-stream allocation order) so the published table stays
        // bit-reproducible.
        OnOffHelper onoff("ns3::UdpSocketFactory",
                          InetSocketAddress(mecAddr, ulPort));
        onoff.SetAttribute("DataRate",   StringValue(drStr.str()));
        onoff.SetAttribute("PacketSize", UintegerValue(pktSize));
        onoff.SetAttribute("OnTime",
            StringValue("ns3::ConstantRandomVariable[Constant="
                        + std::to_string(onTimeSec) + "]"));
        onoff.SetAttribute("OffTime",
            StringValue("ns3::ExponentialRandomVariable[Mean="
                        + std::to_string(meanOffTime) + "]"));

        for (uint32_t u = 0; u < nUes; u++) {
            ApplicationContainer a = onoff.Install(ueNodes.Get(u));
            a.Start(Seconds(warmup + u * stagger));
            a.Stop(Seconds(warmup + simTime));
            clientApps.Add(a);
        }
    } else {
        for (uint32_t u = 0; u < nUes; u++) {
            // Per-UE payload: one packet per Poisson arrival, so the on-time
            // must equal this UE's packet transmission time at the DataRate.
            double onSecU  = uePktSize[u] * 8.0 / dataRateBps;
            double offSecU = (1.0 / lambda) - onSecU;
            if (offSecU <= 0) offSecU = 1.0 / lambda;

            OnOffHelper onoff("ns3::UdpSocketFactory",
                              InetSocketAddress(mecAddr, ulPort));
            onoff.SetAttribute("DataRate",   StringValue(drStr.str()));
            onoff.SetAttribute("PacketSize", UintegerValue(uePktSize[u]));
            onoff.SetAttribute("OnTime",
                StringValue("ns3::ConstantRandomVariable[Constant="
                            + std::to_string(onSecU) + "]"));
            onoff.SetAttribute("OffTime",
                StringValue("ns3::ExponentialRandomVariable[Mean="
                            + std::to_string(offSecU) + "]"));

            ApplicationContainer a = onoff.Install(ueNodes.Get(u));
            a.Start(Seconds(warmup + u * stagger));
            a.Stop(Seconds(warmup + simTime));
            clientApps.Add(a);
        }
    }

    // ── Flow monitor (all nodes — required for end-to-end delay) ────────────
    FlowMonitorHelper fmHelper;
    Ptr<FlowMonitor> monitor = fmHelper.InstallAll();

    Simulator::Stop(Seconds(warmup + simTime + 0.1));
    Simulator::Run();

    // ── Collect statistics (UL flows only) ───────────────────────────────────
    monitor->CheckForLostPackets();
    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(fmHelper.GetClassifier());

    double   delaySumMs = 0.0;   // packet-weighted (P-K predicts the packet mean)
    double   flowAvgSum = 0.0;   // flow-averaged (legacy MD1 reporting)
    uint64_t totalPkts = 0;
    uint32_t nFlows    = 0;

    for (auto& kv : monitor->GetFlowStats()) {
        Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow(kv.first);
        // UL flows: destination = MEC
        if (t.destinationAddress == mecAddr && kv.second.rxPackets > 0) {
            delaySumMs += kv.second.delaySum.GetMilliSeconds();
            flowAvgSum += kv.second.delaySum.GetMilliSeconds()
                          / kv.second.rxPackets;
            totalPkts  += kv.second.rxPackets;
            nFlows++;
        }
    }

    // Homogeneous (MD1) mode keeps the legacy flow-averaged statistic so the
    // published table stays bit-reproducible; heterogeneous payloads use the
    // packet-weighted mean, which is what P-K predicts.
    double meanDelayMs = -1.0;
    if (nFlows > 0) {
        meanDelayMs = randomPayload ? delaySumMs / totalPkts
                                    : flowAvgSum / nFlows;
    }
    double cUlBps        = bottleMbps * 1e6;
    double theoryDelayMs = randomPayload
        ? MG1Sojourn((double)nUes, lambda, ESbytes, ES2bytes2, cUlBps)
        : MD1Sojourn((double)nUes, lambda, pktSize, cUlBps);
    double rho           = nUes * lambda * ESbytes * 8.0 / cUlBps;

    // ── Output ───────────────────────────────────────────────────────────────
    std::cout << "RESULT"
              << " N="         << nUes
              << " mode="      << (randomPayload ? "MG1" : "MD1")
              << " sim_ms="    << std::fixed << std::setprecision(2) << meanDelayMs
              << " theory_ms=" << std::fixed << std::setprecision(2) << theoryDelayMs
              << " rho="       << std::fixed << std::setprecision(4) << rho
              << " ES_bytes="  << std::fixed << std::setprecision(0) << ESbytes
              << " nflows="    << nFlows
              << " pkts="      << totalPkts
              << std::endl;

    Simulator::Destroy();
    return 0;
}
