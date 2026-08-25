function [summary, paired, ci95, manifest] = merge_nr_shards_r2026a_compat(profile, options)
%MERGE_NR_SHARDS_R2026A_COMPAT Merge frozen shards with a scalar assert fix.
%
% This post-processing compatibility tool preserves and verifies the source
% bundle recorded by every shard.  It differs from the frozen merger only in
% reducing the final finite-value check over all numeric paired-table cells.
% Its own digest and the reason for using it are recorded separately in the
% aggregate manifest; it is not part of, or substituted for, the shard source
% bundle fingerprint.

arguments
    profile (1,1) string {mustBeMember(profile, ...
        ["smoke", "quick", "medium", "formal"])}
    options.ShardRoot (1,1) string = ""
    options.OutputRoot (1,1) string = ""
    options.RequireComplete (1,1) logical = true
end

if string(version("-release")) ~= "2026a"
    error("merge_nr_shards_r2026a_compat:WrongMATLAB", ...
        "Merge must run under MATLAB R2026a.");
end
sourceRoot = fileparts(mfilename("fullpath"));
cfg = nr_validation_config(profile);
fingerprint = nr_experiment_fingerprint(cfg, sourceRoot);
if options.ShardRoot == ""
    shardRoot = fullfile(sourceRoot, "results", "shards", ...
        cfg.ExperimentVersion, profile);
else
    shardRoot = options.ShardRoot;
end
if options.OutputRoot == ""
    outputRoot = fullfile(sourceRoot, "results", "aggregates", ...
        cfg.ExperimentVersion, profile);
else
    outputRoot = options.OutputRoot;
end
if ~isfolder(shardRoot)
    error("merge_nr_shards_r2026a_compat:MissingShardRoot", ...
        "Shard root does not exist: %s", shardRoot);
end
if isfolder(outputRoot) || isfile(outputRoot)
    error("merge_nr_shards_r2026a_compat:ExistingOutput", ...
        "Aggregate output already exists; choose a clean path: %s", outputRoot);
end

expectedCount = numel(cfg.BackgroundUEs) * numel(cfg.Seeds);
caseEntries = repmat(struct("name", "", "background_ues", 0, ...
    "seed", 0, "found", false, "shard_path", ""), expectedCount, 1);
expectedNames = strings(expectedCount, 1);
summaryCells = cell(expectedCount, 1);
entryIdx = 0;
for numBackground = cfg.BackgroundUEs
    for seed = cfg.Seeds
        entryIdx = entryIdx + 1;
        caseName = string(sprintf("bg%04d_seed%04d", numBackground, seed));
        casePath = fullfile(shardRoot, caseName);
        expectedNames(entryIdx) = caseName;
        caseEntries(entryIdx).name = caseName;
        caseEntries(entryIdx).background_ues = numBackground;
        caseEntries(entryIdx).seed = seed;
        caseEntries(entryIdx).shard_path = string(casePath);
        if isfolder(casePath)
            validate_case(casePath, cfg, fingerprint, numBackground, seed);
            verify_nr_results(profile, OutputRoot=casePath);
            summaryCells{entryIdx} = readtable(fullfile(casePath, ...
                "summary.csv"), TextType="string");
            caseEntries(entryIdx).found = true;
        end
    end
end

listing = dir(shardRoot);
actualNames = string({listing([listing.isdir]).name})';
actualNames = actualNames(startsWith(actualNames, "bg"));
extraNames = setdiff(actualNames, expectedNames);
if ~isempty(extraNames)
    error("merge_nr_shards_r2026a_compat:ExtraCases", ...
        "Unexpected case shards: %s", strjoin(extraNames, ", "));
end
foundMask = [caseEntries.found]';
missingNames = expectedNames(~foundMask);
if options.RequireComplete && ~isempty(missingNames)
    error("merge_nr_shards_r2026a_compat:MissingCases", ...
        "Expected shards are missing: %s", strjoin(missingNames, ", "));
end
if ~any(foundMask)
    error("merge_nr_shards_r2026a_compat:NoCases", ...
        "No complete case shards were found.");
end

summary = vertcat(summaryCells{foundMask});
parent = fileparts(outputRoot);
if ~isfolder(parent)
    mkdir(parent);
end
stagingRoot = string(tempname(parent));
mkdir(stagingRoot);
cleanup = onCleanup(@() remove_staging_if_present(stagingRoot));
[summary, paired, ci95] = aggregate_paired_results( ...
    stagingRoot, Summary=summary);

for idx = 1:height(ci95)
    expectedN = numel(cfg.Seeds);
    ci95.expected_num_paired_seeds(idx) = expectedN;
    ci95.missing_num_paired_seeds(idx) = ...
        expectedN - ci95.num_paired_seeds(idx);
end
if options.RequireComplete && any(ci95.missing_num_paired_seeds ~= 0)
    error("merge_nr_shards_r2026a_compat:IncompleteCI", ...
        "A complete merge cannot reduce the expected paired-seed count.");
end
writetable(ci95, fullfile(stagingRoot, "paired_ci95.csv"));

manifest = struct();
manifest.complete = isempty(missingNames);
manifest.require_complete = options.RequireComplete;
manifest.experiment_version = cfg.ExperimentVersion;
manifest.profile = profile;
manifest.expected_case_count = expectedCount;
manifest.found_case_count = sum(foundMask);
manifest.missing_case_count = sum(~foundMask);
manifest.expected_arm_count = 2 * expectedCount;
manifest.found_arm_count = height(summary);
manifest.expected_background_ues = cfg.BackgroundUEs;
manifest.expected_seeds = cfg.Seeds;
manifest.expected_arm_names = cfg.ArmNames;
manifest.expected_full_ul_slots = cfg.FullULSlots;
manifest.cases = caseEntries;
manifest.missing_cases = missingNames;
manifest.source_shard_root = string(shardRoot);
manifest.config_sha256 = fingerprint.config_sha256;
manifest.source_bundle_sha256 = fingerprint.source_bundle_sha256;
manifest.aggregation_provenance = aggregation_provenance(sourceRoot);
manifest.generated_utc = string(datetime("now", TimeZone="UTC", ...
    Format="yyyy-MM-dd'T'HH:mm:ss'Z'"));
write_json(fullfile(stagingRoot, "aggregate_manifest.json"), manifest);

payload = struct();
payload.generated_utc = manifest.generated_utc;
payload.matlab_version = string(version);
payload.matlab_release = string(version("-release"));
payload.config = cfg;
payload.aggregate_manifest = manifest;
payload.runs = table2struct(summary);
payload.paired_differences = table2struct(paired);
payload.paired_ci95 = table2struct(ci95);
write_json(fullfile(stagingRoot, "summary.json"), payload);
save(fullfile(stagingRoot, "results.mat"), "summary", "cfg", ...
    "paired", "ci95", "manifest", "fingerprint");

validate_aggregate(summary, paired, ci95, cfg, manifest);
[moved, message] = movefile(stagingRoot, outputRoot);
if ~moved
    error("merge_nr_shards_r2026a_compat:AtomicPublish", ...
        "Could not publish aggregate %s: %s", outputRoot, message);
end
clear cleanup
fprintf("Published compatibility aggregate %s (%d/%d cases).\n", ...
    outputRoot, manifest.found_case_count, manifest.expected_case_count);
end

function validate_case(caseRoot, cfg, fingerprint, numBackground, seed)
path = fullfile(caseRoot, "case_manifest.json");
assert(isfile(path), "Missing case manifest: %s", path);
caseManifest = jsondecode(fileread(path));
assert(caseManifest.complete);
assert(string(caseManifest.experiment_version) == cfg.ExperimentVersion);
assert(string(caseManifest.profile) == cfg.Profile);
assert(caseManifest.background_ues == numBackground);
assert(caseManifest.inference_ues == cfg.InferenceUEs);
assert(caseManifest.seed == seed);
assert(string(caseManifest.config_sha256) == fingerprint.config_sha256);
assert(string(caseManifest.source_bundle_sha256) == ...
    fingerprint.source_bundle_sha256);
assert(isequal(sort(caseManifest.expected_full_ul_slots(:))', ...
    sort(cfg.FullULSlots)));
assert(isequal(sort(string(caseManifest.expected_arm_names(:)))', ...
    sort(cfg.ArmNames)));
end

function validate_aggregate(summary, paired, ci95, cfg, manifest)
assert(height(summary) == 2 * manifest.found_case_count);
assert(height(paired) == manifest.found_case_count);
assert(all(summary.profile == cfg.Profile));
assert(all(summary.experiment_version == cfg.ExperimentVersion));
assert(all(summary.cohort_latency_reliable));
assert(all(summary.receiver_window_latency_reliable));
assert(all(summary.ul_mcs_distribution_reliable));
assert(all(isfinite(paired{:, vartype("numeric")}), "all"), ...
    "Paired output contains a nonfinite numeric effect.");
assert(all(ci95.num_paired_seeds + ci95.missing_num_paired_seeds == ...
    ci95.expected_num_paired_seeds));
end

function provenance = aggregation_provenance(sourceRoot)
compatPath = fullfile(sourceRoot, "merge_nr_shards_r2026a_compat.m");
wrapperPath = fullfile(sourceRoot, "merge_r2026a_compat.ps1");
assert(isfile(wrapperPath), "Compatibility wrapper is missing: %s", wrapperPath);
provenance = struct();
provenance.tool = "merge_nr_shards_r2026a_compat.m";
provenance.tool_sha256 = sha256_file(compatPath);
provenance.wrapper = "merge_r2026a_compat.ps1";
provenance.wrapper_sha256 = sha256_file(wrapperPath);
provenance.original_merge_sha256 = sha256_file(fullfile(sourceRoot, ...
    "merge_nr_shards.m"));
provenance.aggregate_paired_results_sha256 = sha256_file(fullfile( ...
    sourceRoot, "aggregate_paired_results.m"));
provenance.reason = [ ...
    "The frozen merger reached final aggregate validation but MATLAB R2026a " ...
    "rejected its vector-valued assert condition before atomic publication."];
provenance.code_change = [ ...
    "Only the final paired numeric finite-value check is reduced over all " ...
    "table cells with all(..., 'all')."];
provenance.original_attempt_published_output = false;
end

function write_json(path, value)
encoded = jsonencode(value, PrettyPrint=true);
fid = fopen(path, "w", "n", "UTF-8");
if fid < 0
    error("merge_nr_shards_r2026a_compat:JSONOpen", ...
        "Cannot open %s.", path);
end
cleanup = onCleanup(@() fclose(fid));
fwrite(fid, encoded, "char");
fwrite(fid, newline, "char");
clear cleanup
end

function text = sha256_file(path)
fid = fopen(path, "rb");
if fid < 0
    error("merge_nr_shards_r2026a_compat:HashOpen", ...
        "Cannot open %s.", path);
end
cleanup = onCleanup(@() fclose(fid));
bytes = fread(fid, Inf, "*uint8");
clear cleanup
engine = java.security.MessageDigest.getInstance("SHA-256");
engine.update(int8(bytes));
digest = typecast(engine.digest(), "uint8");
text = lower(string(reshape(dec2hex(digest, 2)', 1, [])));
end

function remove_staging_if_present(path)
if isfolder(path)
    rmdir(path, "s");
end
end
