function summary = run_nr_validation(profile, options)
%RUN_NR_VALIDATION Run selected cases into independent atomic shards.
%
% This function never writes a shared aggregate directory. Every
% background-load/seed case receives its own shard so separate MATLAB
% processes can run different cases concurrently. Use merge_nr_shards in a
% single process after the expected shard manifest is complete.

arguments
    profile (1,1) string {mustBeMember(profile, ...
        ["smoke", "quick", "medium", "formal"])} = "smoke"
    options.Seeds (1,:) double {mustBeInteger, mustBePositive} = []
    options.BackgroundUEs (1,:) double {mustBeInteger, mustBePositive} = []
    options.OutputRoot (1,1) string = ""
    options.ReuseComplete (1,1) logical = true
end

if string(version("-release")) ~= "2026a"
    error("run_nr_validation:WrongMATLAB", ...
        "This pilot must run under MATLAB R2026a; current release is %s.", ...
        version("-release"));
end

root = fileparts(mfilename("fullpath"));
addpath(root);
baseCfg = nr_validation_config(profile);
selectedSeeds = baseCfg.Seeds;
selectedLoads = baseCfg.BackgroundUEs;
if ~isempty(options.Seeds)
    selectedSeeds = options.Seeds;
end
if ~isempty(options.BackgroundUEs)
    if ~all(ismember(options.BackgroundUEs, baseCfg.BackgroundUEs))
        error("run_nr_validation:BackgroundUEs", ...
            "Requested loads must be selected from preset values: %s", ...
            mat2str(baseCfg.BackgroundUEs));
    end
    selectedLoads = options.BackgroundUEs;
end

if options.OutputRoot == ""
    shardRoot = fullfile(root, "results", "shards", ...
        baseCfg.ExperimentVersion, profile);
else
    shardRoot = options.OutputRoot;
end
if ~isfolder(shardRoot)
    mkdir(shardRoot);
end

selectedTables = cell(numel(selectedLoads) * numel(selectedSeeds), 1);
selectedIdx = 0;
for numBackground = selectedLoads
    for seed = selectedSeeds
        selectedIdx = selectedIdx + 1;
        caseName = sprintf("bg%04d_seed%04d", numBackground, seed);
        caseRoot = fullfile(shardRoot, caseName);
        caseCfg = baseCfg;
        caseCfg.BackgroundUEs = numBackground;
        caseCfg.Seeds = seed;

        if isfolder(caseRoot)
            if ~options.ReuseComplete
                error("run_nr_validation:ExistingShard", ...
                    "Shard already exists: %s", caseRoot);
            end
            verify_case_manifest(caseRoot, caseCfg, numBackground, seed);
            verify_nr_results(profile, OutputRoot=caseRoot);
            selectedTables{selectedIdx} = readtable( ...
                fullfile(caseRoot, "summary.csv"), TextType="string");
            fprintf("Reused complete shard %s\n", caseRoot);
            continue
        end

        lockPath = caseRoot + ".lock";
        lockCleanup = acquire_case_lock(lockPath); %#ok<NASGU>
        stagingRoot = string(tempname(shardRoot));
        mkdir(stagingRoot);
        cleanup = onCleanup(@() remove_staging_if_present(stagingRoot));
        caseSummary = run_case(stagingRoot, caseCfg, ...
            numBackground, seed);
        verify_case_manifest(stagingRoot, caseCfg, numBackground, seed);
        verify_nr_results(profile, OutputRoot=stagingRoot);

        if isfolder(caseRoot) || isfile(caseRoot)
            error("run_nr_validation:ConcurrentPublish", ...
                "Another process published the shard while this case ran: %s", ...
                caseRoot);
        end
        [moved, message] = movefile(stagingRoot, caseRoot);
        if ~moved
            error("run_nr_validation:AtomicPublish", ...
                "Could not publish shard %s: %s", caseRoot, message);
        end
        clear cleanup
        clear lockCleanup
        selectedTables{selectedIdx} = caseSummary;
        fprintf("Published complete shard %s\n", caseRoot);
    end
end

summary = vertcat(selectedTables{:});
summary = sortrows(summary, ["background_ues", "seed", "full_ul_slots"]);
fprintf("No shared aggregate was written. After all expected shards finish, run merge_nr_shards('%s').\n", profile);
end

function summary = run_case(caseRoot, cfg, numBackground, seed)
sourceRoot = fileparts(mfilename("fullpath"));
fingerprint = nr_experiment_fingerprint(cfg, sourceRoot);
numUEs = numBackground + cfg.InferenceUEs;
positions = generate_ue_positions(numUEs, seed, cfg);
linkState = generate_umi_link_state(positions, seed, cfg);

linkRoot = fullfile(caseRoot, "link_states");
pairRoot = fullfile(caseRoot, "pairs");
auditRoot = fullfile(caseRoot, "arm_audits");
mkdir(linkRoot);
mkdir(pairRoot);
mkdir(auditRoot);
writetable(link_state_table(linkState, numBackground), ...
    fullfile(linkRoot, "link_state.csv"));

pairCells = cell(numel(cfg.ArmNames), 1);
for armIdx = 1:numel(cfg.ArmNames)
    armName = cfg.ArmNames(armIdx);
    fprintf("[%s] background=%d inference=%d seed=%d arm=%s\n", ...
        cfg.Profile, numBackground, cfg.InferenceUEs, seed, armName);
    [newRow, armAudit] = run_nr_arm(cfg, numBackground, ...
        cfg.InferenceUEs, seed, armName, linkState);
    pairCells{armIdx} = newRow;
    writetable(armAudit.MCSHistogram, fullfile(auditRoot, ...
        "ul_mcs_histogram_" + armName + ".csv"));
    writetable(armAudit.SDULatencies, fullfile(auditRoot, ...
        "sdu_latencies_" + armName + ".csv"));
    fprintf("  cohort mean/p95=%.3f/%.3f ms, completion=%.3f, delivered=%.3f Mbps, BLER=%.4f, MCS(grant/PRB)=%.2f/%.2f, wall=%.1f s\n", ...
        newRow.cohort_completed_mean_ul_latency_ms, ...
        newRow.cohort_completed_p95_ul_latency_ms, ...
        newRow.cohort_completion_fraction_packets, ...
        newRow.cohort_delivered_mbps_normalized_by_generation_window, ...
        newRow.bler, ...
        newRow.ul_mcs_mean_grant_weighted, ...
        newRow.ul_mcs_mean_prb_weighted, newRow.wall_time_s);
end

pair = struct2table([pairCells{:}]);
pair = sortrows(pair, "full_ul_slots");
validate_pair(pair);
writetable(pair, fullfile(pairRoot, sprintf( ...
    "pair_bg%04d_seed%04d.csv", numBackground, seed)));
[summary, pairedDifferences, pairedCI95] = ...
    aggregate_paired_results(caseRoot);

manifest = struct();
manifest.complete = true;
manifest.experiment_version = cfg.ExperimentVersion;
manifest.profile = cfg.Profile;
manifest.background_ues = numBackground;
manifest.inference_ues = cfg.InferenceUEs;
manifest.seed = seed;
manifest.expected_full_ul_slots = cfg.FullULSlots;
manifest.expected_arm_names = cfg.ArmNames;
manifest.config_sha256 = fingerprint.config_sha256;
manifest.source_bundle_sha256 = fingerprint.source_bundle_sha256;
manifest.source_files = fingerprint.source_files;
manifest.matlab_release = string(version("-release"));
manifest.generated_utc = string(datetime("now", TimeZone="UTC", ...
    Format="yyyy-MM-dd'T'HH:mm:ss'Z'"));
write_json(fullfile(caseRoot, "case_manifest.json"), manifest);

payload = struct();
payload.generated_utc = manifest.generated_utc;
payload.matlab_version = string(version);
payload.matlab_release = string(version("-release"));
payload.config = cfg;
payload.case_manifest = manifest;
payload.runs = table2struct(summary);
payload.paired_differences = table2struct(pairedDifferences);
payload.paired_ci95 = table2struct(pairedCI95);
write_json(fullfile(caseRoot, "summary.json"), payload);
save(fullfile(caseRoot, "results.mat"), "summary", "cfg", ...
    "pairedDifferences", "pairedCI95", "manifest", "fingerprint");
end

function t = link_state_table(linkState, numBackground)
numUEs = size(linkState.Positions, 1);
ueClass = repmat("inference", numUEs, 1);
ueClass(1:numBackground) = "background";
t = table((1:numUEs)', ueClass, ...
    linkState.Positions(:,1), linkState.Positions(:,2), ...
    linkState.Positions(:,3), linkState.Distance2DM, ...
    linkState.LOSProbability, linkState.LOSUniform, linkState.IsLOS, ...
    linkState.BasePathLossDb, linkState.ShadowSigmaDb, ...
    linkState.ShadowStandardNormal, linkState.ShadowDb, ...
    linkState.TotalPathLossDb, ...
    VariableNames=["ue_index", "ue_class", "x_m", "y_m", "z_m", ...
    "distance_2d_m", "los_probability", "los_uniform", "is_los", ...
    "base_path_loss_db", "shadow_sigma_db", "shadow_z", ...
    "shadow_db", "total_path_loss_db"]);
end

function validate_pair(pair)
assert(height(pair) == 2, "Each shard must have exactly two TDD arms.");
assert(isequal(sort(pair.full_ul_slots)', [4 11]));
assert(isscalar(unique(pair.traffic_packet_checksum)));
assert(isscalar(unique(pair.traffic_interval_checksum)));
assert(isscalar(unique(pair.position_checksum)));
assert(isscalar(unique(pair.link_state_checksum)));
end

function verify_case_manifest(caseRoot, cfg, numBackground, seed)
path = fullfile(caseRoot, "case_manifest.json");
assert(isfile(path), "Missing complete case manifest: %s", path);
manifest = jsondecode(fileread(path));
assert(manifest.complete);
assert(string(manifest.experiment_version) == cfg.ExperimentVersion);
assert(string(manifest.profile) == cfg.Profile);
assert(manifest.background_ues == numBackground);
assert(manifest.inference_ues == cfg.InferenceUEs);
assert(manifest.seed == seed);
sourceRoot = fileparts(mfilename("fullpath"));
fingerprint = nr_experiment_fingerprint(cfg, sourceRoot);
assert(string(manifest.config_sha256) == fingerprint.config_sha256);
assert(string(manifest.source_bundle_sha256) == ...
    fingerprint.source_bundle_sha256);
assert(isequal(sort(manifest.expected_full_ul_slots(:))', ...
    sort(cfg.FullULSlots)));
assert(isequal(sort(string(manifest.expected_arm_names(:)))', ...
    sort(cfg.ArmNames)));
end

function write_json(path, value)
encoded = jsonencode(value, PrettyPrint=true);
fid = fopen(path, "w", "n", "UTF-8");
if fid < 0
    error("run_nr_validation:JSONOpen", "Cannot open %s for writing.", path);
end
cleanup = onCleanup(@() fclose(fid));
fwrite(fid, encoded, "char");
fwrite(fid, newline, "char");
clear cleanup
end

function remove_staging_if_present(path)
if isfolder(path)
    rmdir(path, "s");
end
end

function cleanup = acquire_case_lock(lockPath)
lockFile = java.io.File(char(lockPath));
if ~lockFile.createNewFile()
    error("run_nr_validation:CaseLocked", ...
        "Case lock already exists: %s", lockPath);
end
cleanup = onCleanup(@() remove_lock_if_present(lockPath));
end

function remove_lock_if_present(path)
if isfile(path)
    delete(path);
end
end
