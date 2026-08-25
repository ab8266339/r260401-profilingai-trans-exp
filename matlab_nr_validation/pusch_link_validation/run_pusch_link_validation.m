function [summary, perSeed, awgnSanity] = run_pusch_link_validation(profile, options)
%RUN_PUSCH_LINK_VALIDATION Run and serialize the PUSCH link cross-check.

arguments
    profile (1,1) string {mustBeMember(profile,["smoke","formal"])} = "smoke"
    options.OutputRoot (1,1) string = ""
end

if string(version("-release")) ~= "2026a"
    error("puschLink:WrongMATLAB", ...
        "This cross-check requires MATLAB R2026a; current release is %s.", ...
        version("-release"));
end

root = fileparts(mfilename("fullpath"));
addpath(root);
cfg = pusch_link_config(profile);
if options.OutputRoot == ""
    outputRoot = fullfile(root,"results",profile);
else
    outputRoot = options.OutputRoot;
end
if ~isfolder(outputRoot)
    mkdir(outputRoot);
end

fprintf("Running AWGN sanity at %.1f dB...\n",cfg.AWGNSanitySNRdB);
awgnCells = cell(numel(cfg.MCS),1);
for mcsIdx = 1:numel(cfg.MCS)
    sanityCfg = cfg;
    sanityCfg.ChannelProfile = "None";
    sanityCfg.DelaySpreadSeconds = 0;
    sanityCfg.MaximumDopplerHz = 0;
    sanityCfg.TBPerSeed = cfg.AWGNSanityTBPerMCS;
    sanityCfg.PointRole = "awgn_sanity";
    awgnCells{mcsIdx} = run_pusch_point(sanityCfg,cfg.MCS(mcsIdx), ...
        cfg.AWGNSanitySNRdB,1);
end
awgnSanity = struct2table([awgnCells{:}]);
assert(all(awgnSanity.initial_block_errors == 0), ...
    "High-SNR AWGN sanity produced an initial block error.");
assert(all(awgnSanity.residual_block_errors == 0), ...
    "High-SNR AWGN sanity produced a residual block error.");

numRows = 0;
for mcsIdx = 1:numel(cfg.MCS)
    for snrDB = cfg.MCS(mcsIdx).SNRdB
        pointSeeds = point_design(cfg,cfg.MCS(mcsIdx).Index,snrDB);
        numRows = numRows + numel(pointSeeds);
    end
end
rowCells = cell(numRows,1);
rowIndex = 0;
for mcsIdx = 1:numel(cfg.MCS)
    mcs = cfg.MCS(mcsIdx);
    for snrDB = mcs.SNRdB
        [pointSeeds,tbPerSeed,pointRole] = point_design(cfg,mcs.Index,snrDB);
        pointCfg = cfg;
        pointCfg.TBPerSeed = tbPerSeed;
        pointCfg.PointRole = pointRole;
        for seed = pointSeeds
            rowIndex = rowIndex + 1;
            fprintf("[%s/%s] MCS=%d (%s, R=%.4f), SNR=%+.1f dB, seed=%d, TB=%d\n", ...
                profile,pointRole,mcs.Index,mcs.Modulation,mcs.TargetCodeRate, ...
                snrDB,seed,tbPerSeed);
            rowCells{rowIndex} = run_pusch_point(pointCfg,mcs,snrDB,seed);
            row = rowCells{rowIndex};
            fprintf("  TB=%d, initial BLER=%.3f, residual BLER=%.3f, rate=%.2f Mbps\n", ...
                row.num_transport_blocks,row.initial_bler,row.residual_bler_after_harq, ...
                row.supported_full_ul_rate_mbps);
        end
    end
end
perSeed = struct2table([rowCells{:}]);
perSeed = sortrows(perSeed,["mcs_index","snr_db","seed"]);
summary = aggregate_points(perSeed,cfg);

csvPath = fullfile(outputRoot,"summary.csv");
perSeedPath = fullfile(outputRoot,"per_seed.csv");
sanityPath = fullfile(outputRoot,"awgn_sanity.csv");
jsonPath = fullfile(outputRoot,"summary.json");
matPath = fullfile(outputRoot,"results.mat");
writetable(summary,csvPath);
writetable(perSeed,perSeedPath);
writetable(awgnSanity,sanityPath);

figurePaths = plot_pusch_link_results(summary,cfg,root);
payload = struct();
payload.generated_utc = string(datetime("now",TimeZone="UTC", ...
    Format="yyyy-MM-dd'T'HH:mm:ss'Z'"));
payload.matlab_version = string(version);
payload.matlab_release = string(version("-release"));
payload.claim_scope = "link-level cross-check; not fed back into the abstract-PHY system run";
payload.config = cfg;
payload.summary = table2struct(summary);
payload.per_seed = table2struct(perSeed);
payload.awgn_sanity = table2struct(awgnSanity);
payload.figure_pdf = figurePaths.PDF;
payload.figure_png = figurePaths.PNG;
write_json(jsonPath,payload);
save(matPath,"summary","perSeed","awgnSanity","cfg","figurePaths");

verify_pusch_link_results(profile,OutputRoot=outputRoot,FigurePaths=figurePaths);
fprintf("Wrote %s\n",csvPath);
end

function summary = aggregate_points(perSeed,cfg)
keys = unique(perSeed(:,["mcs_index","snr_db"]),"rows");
rows = cell(height(keys),1);
for idx = 1:height(keys)
    mask = perSeed.mcs_index == keys.mcs_index(idx) & ...
        perSeed.snr_db == keys.snr_db(idx);
    point = perSeed(mask,:);
    totalTB = sum(point.num_transport_blocks);
    initialErrors = sum(point.initial_block_errors);
    residualErrors = sum(point.residual_block_errors);
    attempts = sum(point.total_harq_attempts);
    deliveredBits = sum(point.delivered_bits);
    [initialLow,initialHigh] = wilson_interval(initialErrors,totalTB);
    [residualLow,residualHigh] = wilson_interval(residualErrors,totalTB);
    elapsedSeconds = attempts * point.slot_duration_seconds(1);
    rateMbps = deliveredBits / elapsedSeconds / 1e6;
    row = struct();
    row.profile = string(cfg.Profile);
    row.claim_scope = "link-level cross-check";
    row.design_role = point.design_role(1);
    row.fixed_tb_per_seed = point.fixed_tb_per_seed(1);
    row.mcs_index = point.mcs_index(1);
    row.modulation = point.modulation(1);
    row.target_code_rate = point.target_code_rate(1);
    row.snr_db = point.snr_db(1);
    row.num_seeds = height(point);
    row.num_transport_blocks = totalTB;
    row.tbs_bits = point.tbs_bits(1);
    row.initial_block_errors = initialErrors;
    row.initial_bler = initialErrors / totalTB;
    row.initial_bler_wilson95_low = initialLow;
    row.initial_bler_wilson95_high = initialHigh;
    row.residual_block_errors = residualErrors;
    row.residual_bler_after_harq = residualErrors / totalTB;
    row.residual_bler_wilson95_low = residualLow;
    row.residual_bler_wilson95_high = residualHigh;
    row.total_harq_attempts = attempts;
    row.harq_retransmissions = attempts - totalTB;
    row.mean_attempts_per_tb = attempts / totalTB;
    row.delivered_bits = deliveredBits;
    row.supported_full_ul_rate_mbps = rateMbps;
    row.nominal_spectral_efficiency_bps_hz = rateMbps * 1e6 / cfg.NominalBandwidthHz;
    row.occupied_spectral_efficiency_bps_hz = rateMbps * 1e6 / cfg.OccupiedBandwidthHz;
    row.rate_at_4_of_20_ul_slots_mbps = rateMbps * 4/20;
    row.rate_at_11_of_20_ul_slots_mbps = rateMbps * 11/20;
    row.no_error_tbs_rate_mbps = point.no_error_tbs_rate_mbps(1);
    row.mean_noise_variance_estimate = sum(point.noise_variance_sum) / attempts;
    row.mean_timing_offset_samples = sum(point.timing_offset_sum_samples) / attempts;
    row.timing_fallbacks = sum(point.timing_fallbacks);
    row.runtime_seconds = sum(point.runtime_seconds);
    row.channel_profile = point.channel_profile(1);
    row.delay_spread_ns = point.delay_spread_ns(1);
    row.maximum_doppler_hz = point.maximum_doppler_hz(1);
    rows{idx} = row;
end
summary = struct2table([rows{:}]);
summary = sortrows(summary,["mcs_index","snr_db"]);
end

function [seeds,tbPerSeed,role] = point_design(cfg,mcsIndex,snrDB)
isAnchor = false;
for idx = 1:numel(cfg.AnchorPoints)
    if cfg.AnchorPoints(idx).MCSIndex == mcsIndex && ...
            cfg.AnchorPoints(idx).SNRdB == snrDB
        isAnchor = true;
        break
    end
end
if isAnchor
    seeds = cfg.AnchorSeeds;
    tbPerSeed = cfg.AnchorTBPerSeed;
    role = "precision_anchor";
else
    seeds = cfg.Seeds;
    tbPerSeed = cfg.TBPerSeed;
    role = "curve_point";
end
end

function [low,high] = wilson_interval(errors,total)
z = 1.959963984540054;
fraction = errors/total;
denominator = 1 + z^2/total;
center = (fraction + z^2/(2*total))/denominator;
radius = z/denominator * sqrt(fraction*(1-fraction)/total + z^2/(4*total^2));
low = max(0,center-radius);
high = min(1,center+radius);
end

function write_json(path,value)
encoded = jsonencode(value,PrettyPrint=true);
fid = fopen(path,"w","n","UTF-8");
if fid < 0
    error("puschLink:JSONOpen","Cannot open %s for writing.",path);
end
cleanup = onCleanup(@() fclose(fid));
fwrite(fid,encoded,"char");
fwrite(fid,newline,"char");
clear cleanup
end
