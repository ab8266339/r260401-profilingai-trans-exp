function [summary, paired, ci95] = aggregate_paired_results(outputRoot, options)
%AGGREGATE_PAIRED_RESULTS Pool pair files and compute paired 95% CIs.
%
% CIs use seed-level differences (11-slot arm minus 4-slot arm), rather
% than treating the two arms as independent samples.

arguments
    outputRoot (1,1) string
    options.Summary table = table()
end

if isempty(options.Summary)
    pairFiles = dir(fullfile(outputRoot, "pairs", "pair_bg*_seed*.csv"));
    if isempty(pairFiles)
        error("aggregate_paired_results:NoPairs", ...
            "No pair files found under %s.", outputRoot);
    end
    tables = cell(numel(pairFiles), 1);
    for idx = 1:numel(pairFiles)
        tables{idx} = readtable(fullfile(pairFiles(idx).folder, ...
            pairFiles(idx).name), TextType="string");
    end
    summary = vertcat(tables{:});
else
    summary = options.Summary;
end
summary = sortrows(summary, ["background_ues", "seed", "full_ul_slots"]);

versions = unique(summary.experiment_version);
if numel(versions) ~= 1
    error("aggregate_paired_results:MixedVersions", ...
        "Pair directory contains mixed experiment versions.");
end

keys = unique(summary(:, ["background_ues", "seed"]), "rows");
pairedCells = cell(height(keys), 1);
for idx = 1:height(keys)
    mask = summary.background_ues == keys.background_ues(idx) & ...
        summary.seed == keys.seed(idx);
    pairRows = summary(mask, :);
    validate_pair(pairRows);
    base = pairRows(pairRows.full_ul_slots == 4, :);
    enhanced = pairRows(pairRows.full_ul_slots == 11, :);

    p = struct();
    p.profile = base.profile;
    p.experiment_version = base.experiment_version;
    p.background_ues = base.background_ues;
    p.inference_ues = base.inference_ues;
    p.seed = base.seed;
    p.link_state_checksum = base.link_state_checksum;
    p.delta_cohort_completed_mean_latency_ms = ...
        enhanced.cohort_completed_mean_ul_latency_ms - ...
        base.cohort_completed_mean_ul_latency_ms;
    p.cohort_completed_mean_latency_reduction_pct = safe_percent( ...
        base.cohort_completed_mean_ul_latency_ms - ...
        enhanced.cohort_completed_mean_ul_latency_ms, ...
        base.cohort_completed_mean_ul_latency_ms);
    p.delta_cohort_completed_p95_latency_ms = ...
        enhanced.cohort_completed_p95_ul_latency_ms - ...
        base.cohort_completed_p95_ul_latency_ms;
    p.cohort_completed_p95_latency_reduction_pct = safe_percent( ...
        base.cohort_completed_p95_ul_latency_ms - ...
        enhanced.cohort_completed_p95_ul_latency_ms, ...
        base.cohort_completed_p95_ul_latency_ms);
    p.delta_cohort_completion_fraction_packets = ...
        enhanced.cohort_completion_fraction_packets - ...
        base.cohort_completion_fraction_packets;
    p.delta_cohort_delivered_mbps_normalized_by_generation_window = ...
        enhanced.cohort_delivered_mbps_normalized_by_generation_window - ...
        base.cohort_delivered_mbps_normalized_by_generation_window;
    p.delta_receiver_window_conditional_mean_latency_ms = ...
        enhanced.receiver_window_conditional_mean_ul_latency_ms - ...
        base.receiver_window_conditional_mean_ul_latency_ms;
    p.delta_bler = enhanced.bler - base.bler;
    p.delta_app_queue_overflow_rate = ...
        enhanced.cohort_app_queue_overflow_rate - ...
        base.cohort_app_queue_overflow_rate;
    p.delta_rlc_reassembly_pdu_drop_fraction = ...
        enhanced.rlc_reassembly_pdu_drop_fraction - ...
        base.rlc_reassembly_pdu_drop_fraction;
    p.delta_harq_retransmission_rate = ...
        enhanced.harq_retransmission_rate - base.harq_retransmission_rate;
    p.delta_ul_mcs_mean_grant_weighted = ...
        enhanced.ul_mcs_mean_grant_weighted - ...
        base.ul_mcs_mean_grant_weighted;
    p.delta_ul_mcs_mean_prb_weighted = ...
        enhanced.ul_mcs_mean_prb_weighted - ...
        base.ul_mcs_mean_prb_weighted;
    p.delta_run_end_tx_rx_byte_imbalance = ...
        enhanced.run_end_tx_rx_byte_imbalance - ...
        base.run_end_tx_rx_byte_imbalance;
    p.paired_wall_time_s = base.wall_time_s + enhanced.wall_time_s;
    pairedCells{idx} = p;
end
paired = struct2table([pairedCells{:}]);
paired = sortrows(paired, ["background_ues", "seed"]);

metricNames = ["delta_cohort_completed_mean_latency_ms", ...
    "cohort_completed_mean_latency_reduction_pct", ...
    "delta_cohort_completed_p95_latency_ms", ...
    "cohort_completed_p95_latency_reduction_pct", ...
    "delta_cohort_completion_fraction_packets", ...
    "delta_cohort_delivered_mbps_normalized_by_generation_window", ...
    "delta_receiver_window_conditional_mean_latency_ms", "delta_bler", ...
    "delta_app_queue_overflow_rate", ...
    "delta_rlc_reassembly_pdu_drop_fraction", ...
    "delta_harq_retransmission_rate", ...
    "delta_ul_mcs_mean_grant_weighted", ...
    "delta_ul_mcs_mean_prb_weighted", ...
    "delta_run_end_tx_rx_byte_imbalance"];
definitions = ["UL55 minus UL20", "100*(UL20-UL55)/UL20", ...
    "UL55 minus UL20", "100*(UL20-UL55)/UL20", ...
    "UL55 minus UL20", "UL55 minus UL20", ...
    "UL55 minus UL20", "UL55 minus UL20", ...
    "UL55 minus UL20", "UL55 minus UL20", "UL55 minus UL20", ...
    "UL55 minus UL20", "UL55 minus UL20", "UL55 minus UL20"];

loads = unique(paired.background_ues);
ciCells = cell(numel(loads) * numel(metricNames), 1);
cellIdx = 0;
for loadIdx = 1:numel(loads)
    loadMask = paired.background_ues == loads(loadIdx);
    for metricIdx = 1:numel(metricNames)
        cellIdx = cellIdx + 1;
        values = paired{loadMask, char(metricNames(metricIdx))};
        if any(~isfinite(values))
            error("aggregate_paired_results:NonfiniteEffect", ...
                "Metric %s contains a nonfinite paired effect.", ...
                metricNames(metricIdx));
        end
        n = numel(values);
        if n > 0
            estimate = mean(values);
        else
            estimate = NaN;
        end
        if n >= 2
            sampleSD = std(values, 0);
            halfWidth = student_t_critical_975(n - 1) * sampleSD / sqrt(n);
            ciLow = estimate - halfWidth;
            ciHigh = estimate + halfWidth;
        else
            sampleSD = NaN;
            ciLow = NaN;
            ciHigh = NaN;
        end
        c = struct();
        c.profile = paired.profile(find(loadMask, 1));
        c.experiment_version = paired.experiment_version(find(loadMask, 1));
        c.background_ues = loads(loadIdx);
        c.metric = metricNames(metricIdx);
        c.effect_definition = definitions(metricIdx);
        c.expected_num_paired_seeds = sum(loadMask);
        c.num_paired_seeds = n;
        c.missing_num_paired_seeds = sum(loadMask) - n;
        c.mean_effect = estimate;
        c.sample_sd = sampleSD;
        c.ci95_low = ciLow;
        c.ci95_high = ciHigh;
        c.ci_method = "paired Student-t across independent seeds";
        ciCells{cellIdx} = c;
    end
end
ci95 = struct2table([ciCells{:}]);

writetable(summary, fullfile(outputRoot, "summary.csv"));
writetable(paired, fullfile(outputRoot, "paired_differences.csv"));
writetable(ci95, fullfile(outputRoot, "paired_ci95.csv"));
end

function validate_pair(pair)
assert(height(pair) == 2, "Each load/seed must have exactly two arms.");
assert(isequal(sort(pair.full_ul_slots)', [4 11]));
assert(isscalar(unique(pair.traffic_packet_checksum)));
assert(isscalar(unique(pair.traffic_interval_checksum)));
assert(isscalar(unique(pair.position_checksum)));
assert(isscalar(unique(pair.link_state_checksum)));
end

function result = safe_percent(numerator, denominator)
if denominator ~= 0
    result = 100 * numerator / denominator;
else
    result = NaN;
end
end

function critical = student_t_critical_975(df)
% Two-sided 95% critical values; formal design uses df=4.
tableDf1To30 = [12.706 4.303 3.182 2.776 2.571 2.447 2.365 ...
    2.306 2.262 2.228 2.201 2.179 2.160 2.145 2.131 2.120 ...
    2.110 2.101 2.093 2.086 2.080 2.074 2.069 2.064 2.060 ...
    2.056 2.052 2.048 2.045 2.042];
if df <= 30
    critical = tableDf1To30(df);
elseif df <= 40
    critical = 2.021;
elseif df <= 60
    critical = 2.000;
elseif df <= 120
    critical = 1.980;
else
    critical = 1.960;
end
end
