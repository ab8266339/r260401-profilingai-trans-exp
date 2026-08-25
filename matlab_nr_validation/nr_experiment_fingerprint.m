function fingerprint = nr_experiment_fingerprint(cfg, sourceRoot)
%NR_EXPERIMENT_FINGERPRINT Canonical invariant config and source SHA-256.

arguments
    cfg (1,1) struct
    sourceRoot (1,1) string
end

invariants = rmfield(cfg, ["BackgroundUEs", "Seeds"]);
canonicalConfig = jsonencode(orderfields(invariants));

sourceNames = ["nr_validation_config.m", "generate_ue_positions.m", ...
    "generate_umi_link_state.m", "mmasTraffic.m", "nrSduLatencyLogger.m", ...
    "nrUlMcsLogger.m", "run_nr_arm.m", "run_nr_validation.m", ...
    "aggregate_paired_results.m", "verify_nr_results.m", ...
    "merge_nr_shards.m", "nr_experiment_fingerprint.m"];
sourceFiles = repmat(struct("name", "", "sha256", ""), ...
    numel(sourceNames), 1);
bundleMaterial = canonicalConfig;
for idx = 1:numel(sourceNames)
    path = fullfile(sourceRoot, sourceNames(idx));
    if ~isfile(path)
        error("nr_experiment_fingerprint:MissingSource", ...
            "Fingerprint source is missing: %s", path);
    end
    bytes = read_binary(path);
    digest = sha256_bytes(bytes);
    sourceFiles(idx).name = sourceNames(idx);
    sourceFiles(idx).sha256 = digest;
    bundleMaterial = bundleMaterial + "|" + sourceNames(idx) + "=" + digest;
end

fingerprint = struct();
fingerprint.canonical_config_json = canonicalConfig;
fingerprint.config_sha256 = sha256_bytes(unicode2native( ...
    canonicalConfig, "UTF-8"));
fingerprint.source_files = sourceFiles;
fingerprint.source_bundle_sha256 = sha256_bytes(unicode2native( ...
    bundleMaterial, "UTF-8"));
end

function bytes = read_binary(path)
fid = fopen(path, "rb");
if fid < 0
    error("nr_experiment_fingerprint:Open", "Cannot open %s.", path);
end
cleanup = onCleanup(@() fclose(fid));
bytes = fread(fid, Inf, "*uint8");
clear cleanup
end

function text = sha256_bytes(bytes)
engine = java.security.MessageDigest.getInstance("SHA-256");
engine.update(int8(bytes));
digest = typecast(engine.digest(), "uint8");
text = lower(string(reshape(dec2hex(digest, 2)', 1, [])));
end
