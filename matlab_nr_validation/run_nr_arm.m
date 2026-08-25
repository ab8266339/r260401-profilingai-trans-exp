function [row, audit] = run_nr_arm(cfg, numBackground, numInference, seed, armName, linkState)
%RUN_NR_ARM Run one reproducible TDD NR arm and return scalar metrics.

armIdx = find(cfg.ArmNames == armName, 1);
if isempty(armIdx)
    error("run_nr_arm:UnknownArm", "Unknown arm %s.", armName);
end
fullULSlots = cfg.FullULSlots(armIdx);
positions = linkState.Positions;
numTotalSlots = round(cfg.TDDPeriodicityMs * cfg.SubcarrierSpacingHz / 15000);
numDLSlots = numTotalSlots - fullULSlots - 1; % one special slot

% Reset all MATLAB-global randomness at each arm so paired arms share the
% same internal initialization as well as identical application streams.
rng(seed, "twister");
simulator = wirelessNetworkSimulator.init;

srsPeriodicity = select_srs_periodicity(numBackground + numInference, numTotalSlots);
tdd = struct( ...
    "DLULPeriodicity", cfg.TDDPeriodicityMs, ...
    "NumDLSlots", numDLSlots, ...
    "NumDLSymbols", cfg.NumDLSymbolsSpecial, ...
    "NumULSymbols", cfg.NumULSymbolsSpecial, ...
    "NumULSlots", fullULSlots);

gNB = nrGNB( ...
    Name="gNB", ...
    Position=cfg.GNBPositionM, ...
    CarrierFrequency=cfg.CarrierFrequencyHz, ...
    ChannelBandwidth=cfg.ChannelBandwidthHz, ...
    DuplexMode="TDD", ...
    SubcarrierSpacing=cfg.SubcarrierSpacingHz, ...
    NumResourceBlocks=cfg.NumResourceBlocks, ...
    NumTransmitAntennas=1, ...
    NumReceiveAntennas=1, ...
    PHYModel=cfg.PHYModel, ...
    DLULConfigTDD=tdd, ...
    NumHARQ=cfg.NumHARQ, ...
    SRSPeriodicityUE=srsPeriodicity);
configureScheduler(gNB, Scheduler=cfg.Scheduler, ...
    MaxNumUsersPerTTI=cfg.MaxNumUsersPerTTI);

numUEs = numBackground + numInference;
UEs = nrUE(Position=positions, ...
    NumTransmitAntennas=1, NumReceiveAntennas=1, ...
    TransmitPower=23, PHYModel=cfg.PHYModel);
connectUE(gNB, UEs);

windowEnd = cfg.WarmupSeconds + cfg.MeasureSeconds;
latencyLogger = nrSduLatencyLogger(gNB.TrafficManager);
registerEventCallback(gNB, "AppPacketReceived", ...
    @(event) latencyLogger.capture(event));
slotDurationSeconds = 1 / (cfg.SubcarrierSpacingHz / 15000) * 1e-3;
mcsLogger = nrUlMcsLogger(cfg.WarmupSeconds, windowEnd, ...
    slotDurationSeconds);
mcsListener = addlistener(gNB, "ScheduledResources", ...
    @(~, event) mcsLogger.capture(event)); %#ok<NASGU>

trafficStopSeconds = windowEnd;
packetChecksum = 0;
intervalChecksum = 0;
expectedCohortAttemptedPackets = 0;
expectedCohortAttemptedBytes = 0;
for idx = 1:numUEs
    if idx <= numBackground
        rateHz = cfg.BackgroundRateHz;
        uid = idx - 1;
    else
        rateHz = cfg.InferenceRateHz;
        uid = 10000 + (idx - numBackground - 1);
    end
    trafficSeed = 1000003 * seed + uid;
    traffic = mmasTraffic(rateHz, trafficSeed, trafficStopSeconds);
    packetChecksum = packetChecksum + traffic.PacketChecksum;
    intervalChecksum = intervalChecksum + traffic.IntervalChecksum;
    cohortMask = traffic.GenerationTimesMs > cfg.WarmupSeconds * 1000 & ...
        traffic.GenerationTimesMs <= windowEnd * 1000;
    expectedCohortAttemptedPackets = expectedCohortAttemptedPackets + ...
        sum(cohortMask);
    expectedCohortAttemptedBytes = expectedCohortAttemptedBytes + ...
        sum(traffic.PacketSizes(cohortMask));
    addTrafficSource(UEs(idx), traffic);
end

addNodes(simulator, gNB);
addNodes(simulator, UEs);

% The public nrPathLoss API was used once in generate_umi_link_state to
% create a static, auditable UMi LOS/NLOS plus shadowing realization. The
% exact same realization is passed to both TDD arms in a pair.
addChannelModel(simulator, @(rxInfo, txPacket) ...
    apply_static_umi_link(rxInfo, txPacket, cfg.GNBPositionM, linkState));

wallTimer = tic;
run(simulator, cfg.WarmupSeconds, IsLastStep=false);
before = snapshot(gNB, UEs);
run(simulator, cfg.MeasureSeconds, IsLastStep=false);
generationEnd = snapshot(gNB, UEs);
run(simulator, cfg.DrainSeconds, IsLastStep=true);
afterDrain = snapshot(gNB, UEs);
wallSeconds = toc(wallTimer);

windowD = subtract_snapshots(generationEnd, before);
drainD = subtract_snapshots(afterDrain, generationEnd);
if drainD.AppTxPackets ~= 0 || drainD.AppTxBytes ~= 0 || ...
        drainD.AppQueueOverflowPackets ~= 0
    error("run_nr_arm:TrafficDuringDrain", ...
        "New application traffic was generated during the drain phase.");
end
generatedAttempts = windowD.AppTxPackets + ...
    windowD.AppQueueOverflowPackets;
attemptedPacketCountMismatch = generatedAttempts - ...
    expectedCohortAttemptedPackets;
receiverWindowMeanLatencyMs = safe_divide( ...
    windowD.TotalPacketLatencySeconds, windowD.AppRxPackets) * 1000;
latencyResult = latencyLogger.summarize(cfg.WarmupSeconds, ...
    windowEnd, windowD.AppRxPackets, receiverWindowMeanLatencyMs);
drainEnd = windowEnd + cfg.DrainSeconds;
cohortResult = latencyLogger.summarizeCohort(cfg.WarmupSeconds, ...
    windowEnd, drainEnd, windowD.AppTxPackets, windowD.AppTxBytes);
[mcsResult, mcsHistogram] = mcsLogger.summarize();
mcsNewGrantMismatch = mcsResult.NewGrantCount - ...
    windowD.MACNewTransmissions;
mcsRetxGrantMismatch = mcsResult.RetransmissionGrantCount - ...
    windowD.HARQRetransmissions;
mcsPRBMismatch = mcsResult.TotalScheduledPRBs - ...
    (windowD.ULNewTxRBs + windowD.ULRetxRBs);
mcsCrosscheckPassed = mcsNewGrantMismatch == 0 && ...
    mcsRetxGrantMismatch == 0 && mcsPRBMismatch == 0;

row = struct();
row.profile = cfg.Profile;
row.experiment_version = cfg.ExperimentVersion;
row.seed = seed;
row.background_ues = numBackground;
row.inference_ues = numInference;
row.total_ues = numUEs;
row.arm = armName;
row.full_ul_slots = fullULSlots;
row.slots_per_pattern = numTotalSlots;
row.nominal_full_ul_share = fullULSlots / numTotalSlots;
row.srs_periodicity_slots = srsPeriodicity;
row.warmup_s = cfg.WarmupSeconds;
row.measure_s = cfg.MeasureSeconds;
row.drain_s = cfg.DrainSeconds;
row.priming_marker_packets = numUEs;
row.priming_marker_bytes = numUEs;
row.priming_marker_excluded_from_cohort = true;
row.generated_packets = generatedAttempts;
row.generated_bytes = expectedCohortAttemptedBytes;
row.expected_cohort_attempted_packets = expectedCohortAttemptedPackets;
row.expected_cohort_attempted_bytes = expectedCohortAttemptedBytes;
row.cohort_attempted_packet_count_mismatch = attemptedPacketCountMismatch;
row.cohort_accepted_packets = windowD.AppTxPackets;
row.cohort_accepted_bytes = windowD.AppTxBytes;
row.cohort_app_queue_overflow_packets = windowD.AppQueueOverflowPackets;
row.cohort_app_queue_overflow_rate = safe_divide( ...
    windowD.AppQueueOverflowPackets, generatedAttempts);
row.attempted_offered_mbps = ...
    expectedCohortAttemptedBytes * 8 / cfg.MeasureSeconds / 1e6;
row.accepted_offered_mbps = ...
    windowD.AppTxBytes * 8 / cfg.MeasureSeconds / 1e6;
row.cohort_completed_packets = cohortResult.CompletedPackets;
row.cohort_completed_bytes = cohortResult.CompletedBytes;
row.cohort_unfinished_or_lost_packets = ...
    cohortResult.UnfinishedOrLostPackets;
row.cohort_unfinished_or_lost_bytes = cohortResult.UnfinishedOrLostBytes;
row.cohort_completion_fraction_packets = ...
    cohortResult.CompletionFractionPackets;
row.cohort_completion_fraction_bytes = ...
    cohortResult.CompletionFractionBytes;
row.cohort_completed_mean_ul_latency_ms = ...
    cohortResult.CompletedMeanLatencyMs;
row.cohort_completed_p95_ul_latency_ms = ...
    cohortResult.CompletedP95LatencyMs;
row.cohort_completed_after_generation_window = ...
    cohortResult.CompletedAfterGenerationWindow;
row.cohort_latency_reliable = cohortResult.Reliable;
row.cohort_metric_scope = ...
    "measurement-generated accepted SDUs completed by the end of the finite drain";
row.receiver_window_rx_packets = windowD.AppRxPackets;
row.receiver_window_rx_bytes = windowD.AppRxBytes;
row.receiver_window_conditional_mean_ul_latency_ms = ...
    receiverWindowMeanLatencyMs;
row.receiver_window_conditional_p95_ul_latency_ms = ...
    latencyResult.P95LatencyMs;
row.receiver_window_latency_sample_count = latencyResult.SampleCount;
row.receiver_window_latency_reliable = latencyResult.Reliable;
row.receiver_window_mean_consistency_error_ms = ...
    latencyResult.MeanConsistencyErrorMs;
row.receiver_window_boundary = latencyResult.WindowBoundary;
row.receiver_window_metric_scope = ...
    "secondary conditional metric over SDUs received in the window";
row.receiver_window_goodput_mbps = ...
    windowD.AppRxBytes * 8 / cfg.MeasureSeconds / 1e6;
row.cohort_delivered_mbps_normalized_by_generation_window = ...
    cohortResult.CompletedBytes * 8 / cfg.MeasureSeconds / 1e6;
row.drain_new_app_tx_packets = drainD.AppTxPackets;
row.drain_new_app_tx_bytes = drainD.AppTxBytes;
row.drain_new_app_queue_overflow_packets = drainD.AppQueueOverflowPackets;
row.warmup_end_tx_rx_packet_imbalance = ...
    before.AppTxPackets - before.AppRxPackets;
row.warmup_end_tx_rx_byte_imbalance = before.AppTxBytes - before.AppRxBytes;
row.window_tx_rx_packet_imbalance = ...
    windowD.AppTxPackets - windowD.AppRxPackets;
row.window_tx_rx_byte_imbalance = windowD.AppTxBytes - windowD.AppRxBytes;
row.drain_rx_packets = drainD.AppRxPackets;
row.drain_rx_bytes = drainD.AppRxBytes;
row.run_end_tx_rx_packet_imbalance = ...
    afterDrain.AppTxPackets - afterDrain.AppRxPackets;
row.run_end_tx_rx_byte_imbalance = ...
    afterDrain.AppTxBytes - afterDrain.AppRxBytes;
row.imbalance_scope = "TX-minus-RX counter proxy; not exact queue occupancy";
row.rlc_received_pdus = windowD.RLCReceivedPackets;
row.rlc_reassembly_dropped_pdus = windowD.RLCDroppedPackets;
row.rlc_reassembly_pdu_drop_fraction = safe_divide( ...
    windowD.RLCDroppedPackets, windowD.RLCReceivedPackets);
row.phy_rx_blocks = windowD.PHYReceivedPackets;
row.phy_decode_failures = windowD.PHYDecodeFailures;
row.bler = safe_divide(windowD.PHYDecodeFailures, windowD.PHYReceivedPackets);
row.harq_retransmissions = windowD.HARQRetransmissions;
row.harq_retransmission_rate = safe_divide(windowD.HARQRetransmissions, ...
    windowD.MACNewTransmissions + windowD.HARQRetransmissions);
row.ul_new_tx_rbs = windowD.ULNewTxRBs;
row.ul_retx_rbs = windowD.ULRetxRBs;
row.ul_mcs_grant_count = mcsResult.GrantCount;
row.ul_mcs_new_grant_count = mcsResult.NewGrantCount;
row.ul_mcs_retx_grant_count = mcsResult.RetransmissionGrantCount;
row.ul_mcs_retx_grant_fraction = safe_divide( ...
    mcsResult.RetransmissionGrantCount, mcsResult.GrantCount);
row.ul_mcs_mean_grant_weighted = mcsResult.MeanGrantWeighted;
row.ul_mcs_p50_grant_weighted = mcsResult.P50GrantWeighted;
row.ul_mcs_p95_grant_weighted = mcsResult.P95GrantWeighted;
row.ul_mcs_mean_prb_weighted = mcsResult.MeanPRBWeighted;
row.ul_mcs_p50_prb_weighted = mcsResult.P50PRBWeighted;
row.ul_mcs_p95_prb_weighted = mcsResult.P95PRBWeighted;
row.ul_mcs_total_scheduled_prbs = mcsResult.TotalScheduledPRBs;
row.ul_mcs_new_grant_count_mismatch = mcsNewGrantMismatch;
row.ul_mcs_retx_grant_count_mismatch = mcsRetxGrantMismatch;
row.ul_mcs_scheduled_prb_count_mismatch = mcsPRBMismatch;
row.ul_mcs_distribution_reliable = mcsResult.Reliable && mcsCrosscheckPassed;
row.ul_mcs_observation_scope = ...
    "measurement-window ScheduledResources UL grants";
row.traffic_packet_checksum = packetChecksum;
row.traffic_interval_checksum = intervalChecksum;
row.position_checksum = sum(round(positions(:) * 1000) .* (1:numel(positions))');
row.link_state_checksum = linkState.Checksum;
row.los_ues = sum(linkState.IsLOS);
row.nlos_ues = sum(~linkState.IsLOS);
row.los_fraction = mean(linkState.IsLOS);
row.mean_path_loss_db = mean(linkState.TotalPathLossDb);
row.mean_shadow_db = mean(linkState.ShadowDb);
row.std_shadow_db = std(linkState.ShadowDb);
row.wall_time_s = wallSeconds;
row.matlab_release = string(version("-release"));
row.phy_model = cfg.PHYModel;
row.scheduler = cfg.Scheduler;
row.channel_model = cfg.ChannelModel;

if latencyResult.WindowBoundary == "[start,end]"
    receiverWindowMask = latencyLogger.ReceiptTimesSeconds >= cfg.WarmupSeconds & ...
        latencyLogger.ReceiptTimesSeconds <= windowEnd;
else
    receiverWindowMask = latencyLogger.ReceiptTimesSeconds > cfg.WarmupSeconds & ...
        latencyLogger.ReceiptTimesSeconds <= windowEnd;
end
cohortCompletedMask = latencyLogger.GenerationTimesSeconds > cfg.WarmupSeconds & ...
    latencyLogger.GenerationTimesSeconds <= windowEnd & ...
    latencyLogger.ReceiptTimesSeconds <= drainEnd;
audit = struct();
audit.MCSHistogram = mcsHistogram;
audit.SDULatencies = table(latencyLogger.GenerationTimesSeconds, ...
    latencyLogger.ReceiptTimesSeconds, latencyLogger.PacketLengthsBytes, ...
    latencyLogger.LatenciesMs, receiverWindowMask, cohortCompletedMask, ...
    VariableNames=["generation_time_s", "receipt_time_s", ...
    "packet_length_bytes", "latency_ms", "in_receiver_window", ...
    "in_completed_measurement_generated_cohort"]);
end

function periodicity = select_srs_periodicity(numUEs, patternSlots)
allowed = [5 8 10 16 20 32 40 64 80 160 320 640 1280 2560];
minimum = ceil(numUEs / 16) * patternSlots;
valid = allowed(allowed >= minimum & mod(allowed, patternSlots) == 0);
if isempty(valid)
    error("run_nr_arm:SRSPeriodicity", ...
        "No supported SRS periodicity for %d UEs.", numUEs);
end
periodicity = valid(1);
end

function rxPacket = apply_static_umi_link(rxInfo, txPacket, gNBPosition, linkState)
rxPacket = txPacket;
txPosition = txPacket.TransmitterPosition(:);
rxPosition = rxInfo.Position(:);
if norm(txPosition - gNBPosition(:)) < 1e-6
    uePosition = rxPosition;
else
    uePosition = txPosition;
end
key = sprintf("%.6f|%.6f|%.6f", ...
    uePosition(1), uePosition(2), uePosition(3));
if ~isKey(linkState.KeyToIndex, key)
    error("run_nr_arm:UnknownLink", ...
        "No pre-generated UMi link state for UE position %s.", key);
end
linkIdx = linkState.KeyToIndex(key);
rxPacket.Power = txPacket.Power - linkState.TotalPathLossDb(linkIdx);
% The abstract PHY still requires a complete channel metadata structure.
rxPacket.Metadata.Channel.PathGains = 1;
rxPacket.Metadata.Channel.PathDelays = 0;
rxPacket.Metadata.Channel.PathFilters = 1;
rxPacket.Metadata.Channel.SampleTimes = 0;
end

function s = snapshot(gNB, UEs)
appRx = statistics(gNB.TrafficManager, true);
if isempty(appRx.Destinations)
    totalLatency = 0;
else
    totalLatency = sum([appRx.Destinations.TotalPacketLatency]);
end

appTxPackets = 0;
appTxBytes = 0;
queueOverflow = 0;
for idx = 1:numel(UEs)
    tx = statistics(UEs(idx).TrafficManager, false);
    appTxPackets = appTxPackets + tx.TransmittedPackets;
    appTxBytes = appTxBytes + tx.TransmittedBytes;
    if ~isempty(tx.TrafficSources)
        queueOverflow = queueOverflow + sum([tx.TrafficSources.TransmitQueueOverflow]);
    end
end

g = statistics(gNB);
u = statistics(UEs);
uMAC = [u.MAC];

s = struct();
s.AppTxPackets = appTxPackets;
s.AppTxBytes = appTxBytes;
s.AppQueueOverflowPackets = queueOverflow;
s.AppRxPackets = appRx.ReceivedPackets;
s.AppRxBytes = appRx.ReceivedBytes;
s.TotalPacketLatencySeconds = totalLatency;
s.RLCReceivedPackets = g.RLC.ReceivedPackets;
s.RLCDroppedPackets = g.RLC.DroppedPackets;
s.PHYReceivedPackets = g.PHY.ReceivedPackets;
s.PHYDecodeFailures = g.PHY.DecodeFailures;
s.MACNewTransmissions = sum([uMAC.TransmittedPackets]);
s.HARQRetransmissions = sum([uMAC.Retransmissions]);
s.ULNewTxRBs = sum([uMAC.ULTransmissionRB]);
s.ULRetxRBs = sum([uMAC.ULRetransmissionRB]);
end

function d = subtract_snapshots(a, b)
fields = fieldnames(a);
for idx = 1:numel(fields)
    name = fields{idx};
    d.(name) = a.(name) - b.(name);
end
end

function value = safe_divide(numerator, denominator)
if denominator > 0
    value = numerator / denominator;
else
    value = 0;
end
end
