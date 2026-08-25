function verify_pusch_link_results(profile,options)
%VERIFY_PUSCH_LINK_RESULTS Check schema, counts, units, and output artifacts.

arguments
    profile (1,1) string {mustBeMember(profile,["smoke","formal"])}
    options.OutputRoot (1,1) string
    options.FigurePaths (1,1) struct
end

summary = readtable(fullfile(options.OutputRoot,"summary.csv"), ...
    TextType="string",VariableNamingRule="preserve");
perSeed = readtable(fullfile(options.OutputRoot,"per_seed.csv"), ...
    TextType="string",VariableNamingRule="preserve");
sanity = readtable(fullfile(options.OutputRoot,"awgn_sanity.csv"), ...
    TextType="string",VariableNamingRule="preserve");

required = ["initial_bler","initial_bler_wilson95_low", ...
    "initial_bler_wilson95_high","residual_bler_after_harq", ...
    "supported_full_ul_rate_mbps","nominal_spectral_efficiency_bps_hz", ...
    "rate_at_4_of_20_ul_slots_mbps","rate_at_11_of_20_ul_slots_mbps", ...
    "design_role","fixed_tb_per_seed"];
assert(all(ismember(required,string(summary.Properties.VariableNames))), ...
    "Summary schema is incomplete.");
assert(all(summary.initial_bler >= 0 & summary.initial_bler <= 1));
assert(all(summary.residual_bler_after_harq >= 0 & summary.residual_bler_after_harq <= 1));
assert(all(summary.initial_bler_wilson95_low <= summary.initial_bler));
assert(all(summary.initial_bler <= summary.initial_bler_wilson95_high));
assert(all(summary.residual_block_errors <= summary.initial_block_errors));
assert(all(summary.claim_scope == "link-level cross-check"));
assert(all(perSeed.claim_scope == "link-level cross-check"));
assert(all(summary.supported_full_ul_rate_mbps >= 0));
assert(all(summary.supported_full_ul_rate_mbps <= summary.no_error_tbs_rate_mbps + 1e-9));
assert(all(summary.channel_profile == "TDL-C"));
assert(all(isfinite(summary.mean_noise_variance_estimate) & ...
    summary.mean_noise_variance_estimate > 0));
assert(all(isfinite(summary.mean_timing_offset_samples)));
assert(all(abs(summary.rate_at_4_of_20_ul_slots_mbps - ...
    0.2*summary.supported_full_ul_rate_mbps) < 1e-8));
assert(all(abs(summary.rate_at_11_of_20_ul_slots_mbps - ...
    0.55*summary.supported_full_ul_rate_mbps) < 1e-8));
assert(all(perSeed.num_transport_blocks >= 2));
assert(all(sanity.initial_block_errors == 0 & sanity.residual_block_errors == 0));
expectedRows = 6;
if profile == "formal"
    expectedRows = 18;
    anchors = summary(summary.design_role == "precision_anchor",:);
    curve = summary(summary.design_role == "curve_point",:);
    anchorSeedRows = perSeed(perSeed.design_role == "precision_anchor",:);
    curveSeedRows = perSeed(perSeed.design_role == "curve_point",:);
    assert(height(anchors) == 3,"Formal output must contain three precision anchors.");
    assert(height(curve) == 15,"Formal output must contain fifteen regular curve points.");
    assert(all(anchors.num_transport_blocks == 1000 & anchors.num_seeds == 5 & ...
        anchors.fixed_tb_per_seed == 200), ...
        "Each precision anchor must contain 5 seeds x 200 TB = 1000 TB.");
    assert(all(curve.num_transport_blocks == 180 & curve.num_seeds == 3 & ...
        curve.fixed_tb_per_seed == 60), ...
        "Each regular curve point must contain 3 seeds x 60 TB = 180 TB.");
    assert(height(anchorSeedRows) == 15 & all(anchorSeedRows.num_transport_blocks == 200), ...
        "Precision-anchor seed rows do not match the fixed design.");
    assert(height(curveSeedRows) == 45 & all(curveSeedRows.num_transport_blocks == 60), ...
        "Regular seed rows do not match the fixed design.");
    expectedAnchors = [4 6; 13 15; 18 20];
    actualAnchors = sortrows([anchors.mcs_index anchors.snr_db],[1 2]);
    assert(isequal(actualAnchors,expectedAnchors),"Precision-anchor coordinates changed.");
    assert(sum(summary.num_transport_blocks) == 5700, ...
        "Formal sweep must contain exactly 5700 new transport blocks.");
else
    assert(all(summary.num_transport_blocks == 2), ...
        "Each smoke point must contain two transport blocks.");
    assert(all(summary.design_role == "curve_point"));
end
assert(all(sanity.num_transport_blocks == 3), ...
    "Each AWGN sanity point must contain three transport blocks.");
assert(all(sanity.design_role == "awgn_sanity"));
assert(height(summary) == expectedRows,"Unexpected number of aggregate rows.");
assert(isfile(fullfile(options.OutputRoot,"summary.json")),"JSON output is missing.");
assert(isfile(fullfile(options.OutputRoot,"results.mat")),"MAT output is missing.");
assert(isfile(options.FigurePaths.PDF),"PDF figure is missing.");
assert(isfile(options.FigurePaths.PNG),"PNG figure is missing.");
assert(dir(options.FigurePaths.PDF).bytes > 0,"PDF figure is empty.");
assert(dir(options.FigurePaths.PNG).bytes > 0,"PNG figure is empty.");
fprintf("Verification passed for %s: %d aggregate rows, %d seeded rows.\n", ...
    profile,height(summary),height(perSeed));
end
