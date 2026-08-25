function result = run_pusch_point(cfg,mcs,snrDB,seed)
%RUN_PUSCH_POINT Simulate one SNR/MCS/seed with practical estimation.

started = tic;
carrier = nrCarrierConfig;
carrier.NCellID = cfg.NCellID;
carrier.SubcarrierSpacing = cfg.SubcarrierSpacingKHz;
carrier.CyclicPrefix = "normal";
carrier.NSizeGrid = cfg.NSizeGrid;
carrier.NStartGrid = 0;

pusch = nrPUSCHConfig;
pusch.Modulation = char(mcs.Modulation);
pusch.NumLayers = 1;
pusch.MappingType = "A";
pusch.SymbolAllocation = [0 carrier.SymbolsPerSlot];
pusch.PRBSet = 0:cfg.NSizeGrid-1;
pusch.TransformPrecoding = false;
pusch.TransmissionScheme = "nonCodebook";
pusch.NumAntennaPorts = 1;
pusch.NID = cfg.NCellID;
pusch.RNTI = cfg.RNTI;
pusch.DMRS.DMRSConfigurationType = 1;
pusch.DMRS.DMRSTypeAPosition = 2;
pusch.DMRS.DMRSAdditionalPosition = 1;
pusch.DMRS.DMRSLength = 1;
pusch.DMRS.NumCDMGroupsWithoutData = 2;

carrier.NSlot = 0;
[~,indexInfo] = nrPUSCHIndices(carrier,pusch);
tbs = nrTBS(pusch.Modulation,pusch.NumLayers,numel(indexInfo.PRBSet), ...
    indexInfo.NREPerPRB,mcs.TargetCodeRate,0);
ofdmInfo = nrOFDMInfo(carrier);
slotDuration = 1e-3/carrier.SlotsPerSubframe;

encoder = nrULSCH;
encoder.MultipleHARQProcesses = true;
encoder.TargetCodeRate = mcs.TargetCodeRate;
decoder = nrULSCHDecoder;
decoder.MultipleHARQProcesses = true;
decoder.AutoFlushSoftBuffer = false;
decoder.TargetCodeRate = mcs.TargetCodeRate;
decoder.TransportBlockLength = tbs;
decoder.LDPCDecodingAlgorithm = char(cfg.LDPCAlgorithm);
decoder.MaximumLDPCIterationCount = cfg.MaximumLDPCIterations;

useFadingChannel = cfg.ChannelProfile ~= "None";
if useFadingChannel
    channel = nrTDLChannel;
    channel.DelayProfile = char(cfg.ChannelProfile);
    channel.DelaySpread = cfg.DelaySpreadSeconds;
    channel.MaximumDopplerShift = cfg.MaximumDopplerHz;
    channel.PathGainSampleRate = "auto";
    channel.SampleRate = ofdmInfo.SampleRate;
    channel.NumTransmitAntennas = cfg.NumTxAntennas;
    channel.NumReceiveAntennas = cfg.NumRxAntennas;
    channel.RandomStream = "mt19937ar with seed";
    channel.Seed = cfg.ChannelSeedOffset + seed;
    swapTransmitAndReceive(channel);
    channelInfo = info(channel);
    maxChannelDelay = channelInfo.MaximumChannelDelay;
else
    channel = [];
    maxChannelDelay = 0;
end

% The channel uses a local seeded stream; the global stream controls bits and AWGN.
rng(100000*mcs.Index + 1000*seed,"twister");
snrLinear = 10^(snrDB/10);
noiseScale = 1/sqrt(cfg.NumRxAntennas*double(ofdmInfo.Nfft)*snrLinear);

initialErrors = 0;
residualErrors = 0;
totalAttempts = 0;
deliveredBits = 0;
noiseVarianceSum = 0;
timingOffsetSum = 0;
timingFallbacks = 0;
for numTB = 1:cfg.TBPerSeed
    transportBlock = randi([0 1],tbs,1);
    setTransportBlock(encoder,transportBlock,0);
    resetSoftBuffer(decoder,0);
    blockFailed = true;
    for rvIndex = 1:numel(cfg.RVSequence)
        rv = cfg.RVSequence(rvIndex);
        absoluteSlot = totalAttempts;
        carrier.NSlot = mod(absoluteSlot,carrier.SlotsPerFrame);
        carrier.NFrame = floor(absoluteSlot/carrier.SlotsPerFrame);
        [puschIndices,puschInfo] = nrPUSCHIndices(carrier,pusch);
        dmrsIndices = nrPUSCHDMRSIndices(carrier,pusch);
        dmrsSymbols = nrPUSCHDMRS(carrier,pusch);

        codedBlock = encoder(pusch.Modulation,pusch.NumLayers, ...
            puschInfo.G,rv,0);
        puschSymbols = nrPUSCH(carrier,pusch,codedBlock);
        txGrid = nrResourceGrid(carrier,cfg.NumTxAntennas);
        txGrid(puschIndices) = puschSymbols;
        txGrid(dmrsIndices) = dmrsSymbols;
        txWaveform = nrOFDMModulate(carrier,txGrid);
        paddedWaveform = [txWaveform; zeros(maxChannelDelay,cfg.NumTxAntennas)];
        if useFadingChannel
            rxWaveform = channel(paddedWaveform);
        else
            rxWaveform = paddedWaveform;
        end
        noise = noiseScale * complex(randn(size(rxWaveform)),randn(size(rxWaveform)));
        rxWaveform = rxWaveform + noise;

        referenceGrid = nrResourceGrid(carrier,pusch.NumAntennaPorts);
        referenceGrid(dmrsIndices) = dmrsSymbols;
        estimatedOffset = nrTimingEstimate(carrier,rxWaveform,referenceGrid);
        if isempty(estimatedOffset) || ~isfinite(estimatedOffset) || ...
                estimatedOffset < 0 || estimatedOffset > maxChannelDelay
            estimatedOffset = 0;
            timingFallbacks = timingFallbacks + 1;
        end
        timingOffsetSum = timingOffsetSum + estimatedOffset;
        rxWaveform = rxWaveform(1+estimatedOffset:end,:);
        rxGrid = nrOFDMDemodulate(carrier,rxWaveform);
        if size(rxGrid,2) < carrier.SymbolsPerSlot
            rxGrid(:,end+1:carrier.SymbolsPerSlot,:) = 0;
        elseif size(rxGrid,2) > carrier.SymbolsPerSlot
            rxGrid = rxGrid(:,1:carrier.SymbolsPerSlot,:);
        end

        [channelEstimate,noiseVariance] = nrChannelEstimate(carrier,rxGrid, ...
            dmrsIndices,dmrsSymbols,CDMLengths=pusch.DMRS.CDMLengths);
        noiseVariance = max(real(mean(noiseVariance,"all")),1e-10);
        [rxPUSCH,hPUSCH] = nrExtractResources(puschIndices,rxGrid,channelEstimate);
        [equalizedSymbols,csi] = nrEqualizeMMSE(rxPUSCH,hPUSCH,noiseVariance);
        ulschLLRs = nrPUSCHDecode(carrier,pusch,equalizedSymbols,noiseVariance);
        csi = nrLayerDemap(csi);
        qm = length(ulschLLRs)/length(equalizedSymbols);
        csi = reshape(repmat(csi{1}.',qm,1),[],1);
        ulschLLRs = ulschLLRs .* csi;
        [~,blockError] = decoder(ulschLLRs,pusch.Modulation, ...
            pusch.NumLayers,rv,0);

        totalAttempts = totalAttempts + 1;
        noiseVarianceSum = noiseVarianceSum + noiseVariance;
        blockFailed = logical(blockError);
        if rvIndex == 1 && blockFailed
            initialErrors = initialErrors + 1;
        end
        if ~blockFailed
            deliveredBits = deliveredBits + tbs;
            break
        end
    end
    if blockFailed
        residualErrors = residualErrors + 1;
    end

end

elapsedSeconds = totalAttempts * slotDuration;
rateMbps = deliveredBits/elapsedSeconds/1e6;
result = struct();
result.profile = string(cfg.Profile);
result.claim_scope = "link-level cross-check";
result.design_role = string(cfg.PointRole);
result.fixed_tb_per_seed = cfg.TBPerSeed;
result.mcs_index = mcs.Index;
result.modulation = string(mcs.Modulation);
result.target_code_rate = mcs.TargetCodeRate;
result.snr_db = snrDB;
result.seed = seed;
result.num_transport_blocks = numTB;
result.tbs_bits = tbs;
result.initial_block_errors = initialErrors;
result.initial_bler = initialErrors/numTB;
result.residual_block_errors = residualErrors;
result.residual_bler_after_harq = residualErrors/numTB;
result.total_harq_attempts = totalAttempts;
result.harq_retransmissions = totalAttempts-numTB;
result.mean_attempts_per_tb = totalAttempts/numTB;
result.delivered_bits = deliveredBits;
result.slot_duration_seconds = slotDuration;
result.supported_full_ul_rate_mbps = rateMbps;
result.nominal_spectral_efficiency_bps_hz = rateMbps*1e6/cfg.NominalBandwidthHz;
result.occupied_spectral_efficiency_bps_hz = rateMbps*1e6/cfg.OccupiedBandwidthHz;
result.rate_at_4_of_20_ul_slots_mbps = rateMbps*4/20;
result.rate_at_11_of_20_ul_slots_mbps = rateMbps*11/20;
result.no_error_tbs_rate_mbps = tbs/slotDuration/1e6;
result.noise_variance_sum = noiseVarianceSum;
result.mean_noise_variance_estimate = noiseVarianceSum/totalAttempts;
result.timing_offset_sum_samples = timingOffsetSum;
result.mean_timing_offset_samples = timingOffsetSum/totalAttempts;
result.timing_fallbacks = timingFallbacks;
result.runtime_seconds = toc(started);
result.channel_profile = string(cfg.ChannelProfile);
result.delay_spread_ns = cfg.DelaySpreadSeconds*1e9;
result.maximum_doppler_hz = cfg.MaximumDopplerHz;
result.practical_channel_estimation = true;
result.rv_sequence = join(string(cfg.RVSequence),"-");
end
