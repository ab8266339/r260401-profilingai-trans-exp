function outputs = plot_validation_figure(systemCsvPath, options)
%PLOT_VALIDATION_FIGURE Export the paired system/PUSCH paper figure.
%
% The system input may be an aggregate summary.csv, a two-arm pair CSV, or
% paired_differences.csv.  For paired_differences.csv the sibling summary.csv
% is resolved automatically because the delta-only table does not contain the
% two completion fractions needed to interpret completed-cohort latency.

arguments
    systemCsvPath (1,1) string
    options.PUSCHCsvPath (1,1) string = ""
    options.OutputDirectory (1,1) string = ""
    options.BaseName (1,1) string = ""
    options.TotalUEs (1,:) double {mustBeInteger,mustBePositive} = []
    options.ForcePreview (1,1) logical = false
    options.Visible (1,1) logical = false
end

root = string(fileparts(mfilename("fullpath")));
if strlength(options.PUSCHCsvPath) == 0
    options.PUSCHCsvPath = fullfile(root, "..", "pusch_link_validation", ...
        "results", "formal", "summary.csv");
end
if strlength(options.OutputDirectory) == 0
    options.OutputDirectory = fullfile(root, "output");
end

[systemRows, resolvedSystemCsv] = load_system_rows(systemCsvPath);
puschRows = readtable(options.PUSCHCsvPath, TextType="string");
validate_pusch_rows(puschRows);

[systemRows, loads, previewReasons] = select_and_validate_system_rows( ...
    systemRows, options.TotalUEs);
puschRows = puschRows(ismember(puschRows.mcs_index, [4 13 18]), :);

systemProfiles = lower(unique(systemRows.profile));
puschProfiles = lower(unique(puschRows.profile));
isFormalSystem = numel(systemProfiles) == 1 && systemProfiles == "formal";
isFormalPUSCH = numel(puschProfiles) == 1 && puschProfiles == "formal";
hasTargetLoads = isequal(sort(loads(:))', [208 238]);
hasRequiredSeeds = all(arrayfun(@(x) numel(unique( ...
    systemRows.seed(systemRows.total_ues == x))), loads) == 5);
if ~isFormalSystem
    previewReasons(end+1) = "system profile is not formal";
end
if ~isFormalPUSCH
    previewReasons(end+1) = "PUSCH profile is not formal";
end
if ~hasTargetLoads
    previewReasons(end+1) = "system loads are not both 208 and 238 UEs";
end
if ~hasRequiredSeeds
    previewReasons(end+1) = ...
        "the formal design requires exactly five paired seeds per load";
end
isPreview = options.ForcePreview || ~isempty(previewReasons);
if options.ForcePreview
    previewReasons(end+1) = "ForcePreview was requested";
end

if strlength(options.BaseName) == 0
    if isPreview
        stem = "fig_nr_validation_preview_nonfinal";
    else
        stem = "fig_nr_validation";
    end
else
    stem = options.BaseName;
end

if ~isfolder(options.OutputDirectory)
    mkdir(options.OutputDirectory);
end
pdfPath = fullfile(options.OutputDirectory, stem + ".pdf");
pngPath = fullfile(options.OutputDirectory, stem + ".png");
metadataPath = fullfile(options.OutputDirectory, stem + "_metadata.json");

visibility = "off";
if options.Visible
    visibility = "on";
end
fig = figure(Visible=visibility, Color="white", Units="inches", ...
    Position=[0.5 0.5 7.16 3.55], PaperPositionMode="auto", ...
    Renderer="painters");
cleanupFigure = onCleanup(@() close_if_valid(fig));

blue = [0 114 178] / 255;
vermillion = [213 94 0] / 255;
green = [0 158 115] / 255;
purple = [204 121 167] / 255;
dark = [0.16 0.16 0.16];
gridColor = [0.84 0.84 0.84];
loadColors = [blue; vermillion];

% Panel (a): paired performance plane.  Completion is a primary axis rather
% than a footnote because completed-cohort latency is conditional on delivery.
axA = axes(fig, Position=[0.075 0.205 0.405 0.705]);
hold(axA, "on");
allCompletionPct = 100 * systemRows.cohort_completion_fraction_packets;
allLatencies = systemRows.cohort_completed_p95_ul_latency_ms;
positiveLatencies = allLatencies(isfinite(allLatencies) & allLatencies > 0);
if isempty(positiveLatencies)
    error("plot_validation_figure:NoFiniteLatency", ...
        "No finite positive completed-cohort p95 latency is available.");
end

completionSpan = max(allCompletionPct) - min(allCompletionPct);
if completionSpan < 2
    xLower = max(0, min(allCompletionPct) - 10);
    xUpper = min(100.5, max(allCompletionPct) + 10);
    if xUpper - xLower < 10
        xLower = max(0, xUpper - 10);
    end
else
    completionPad = max(2, 0.08 * completionSpan);
    xLower = max(0, min(allCompletionPct) - completionPad);
    xUpper = min(100.5, max(allCompletionPct) + completionPad);
end
xlim(axA, [xLower xUpper]);

latencyRatio = max(positiveLatencies) / min(positiveLatencies);
if latencyRatio > 10
    set(axA, YScale="log");
    ylim(axA, [min(positiveLatencies)/1.35 max(positiveLatencies)*1.45]);
else
    span = max(positiveLatencies) - min(positiveLatencies);
    if span == 0
        span = max(1, max(positiveLatencies) * 0.2);
    end
    ylim(axA, [max(0, min(positiveLatencies)-0.16*span) ...
        max(positiveLatencies)+0.30*span]);
end

% Paired paths are drawn first; endpoint markers then make the 4-to-11-slot
% direction unambiguous even on a logarithmic latency axis.
for loadIdx = 1:numel(loads)
    loadMask = systemRows.total_ues == loads(loadIdx);
    seeds = unique(systemRows.seed(loadMask));
    color = loadColors(min(loadIdx, size(loadColors,1)), :);
    for seedIdx = 1:numel(seeds)
        pairMask = loadMask & systemRows.seed == seeds(seedIdx);
        pair = sortrows(systemRows(pairMask,:), "full_ul_slots");
        xPair = 100 * pair.cohort_completion_fraction_packets(:)';
        yPair = pair.cohort_completed_p95_ul_latency_ms(:)';
        plot(axA, xPair, yPair, "-", Color=color, LineWidth=1.0, ...
            HandleVisibility="off");
        scatter(axA, xPair(1), yPair(1), 28, "o", ...
            MarkerEdgeColor=color, MarkerFaceColor="white", ...
            LineWidth=1.0, HandleVisibility="off");
        scatter(axA, xPair(2), yPair(2), 34, "d", ...
            MarkerEdgeColor=dark, MarkerFaceColor=color, ...
            LineWidth=0.7, HandleVisibility="off");
    end
end

xlabel(axA, "Accepted measurement-window cohort completion (%)");
ylabel(axA, "95th-percentile completed-cohort latency (ms)");

loadHandles = gobjects(numel(loads), 1);
loadLabels = strings(numel(loads), 1);
for loadIdx = 1:numel(loads)
    color = loadColors(min(loadIdx, size(loadColors,1)), :);
    loadHandles(loadIdx) = plot(axA, nan, nan, "-", Color=color, ...
        LineWidth=1.25, HandleVisibility="on");
    loadLabels(loadIdx) = sprintf("%d devices", loads(loadIdx));
end
arm4Handle = scatter(axA, nan, nan, 28, "o", MarkerEdgeColor=dark, ...
    MarkerFaceColor="white", LineWidth=0.9, HandleVisibility="on");
arm11Handle = scatter(axA, nan, nan, 34, "d", MarkerEdgeColor=dark, ...
    MarkerFaceColor=[0.58 0.58 0.58], LineWidth=0.7, ...
    HandleVisibility="on");
legendHandlesA = [loadHandles; arm4Handle; arm11Handle];
legendLabelsA = [loadLabels; "4 slots"; "11 slots"];
legend(axA, legendHandlesA, legendLabelsA, Location="best", Box="off", ...
    NumColumns=2, FontName="Times New Roman", FontSize=6.4);

text(axA, -0.135, 1.07, "(a)", Units="normalized", ...
    FontName="Times New Roman", FontSize=8.2, Color=dark, ...
    HorizontalAlignment="left", VerticalAlignment="bottom");
if isPreview
    text(axA, 1.0, 1.065, "PREVIEW—non-final system data", ...
        Units="normalized", HorizontalAlignment="right", ...
        VerticalAlignment="bottom", FontName="Times New Roman", ...
        FontSize=6.2, Color=vermillion);
end
grid(axA, "on");
box(axA, "on");

% Panel (b): initial PUSCH BLER with Wilson 95% intervals.
axB = axes(fig, Position=[0.57 0.205 0.405 0.705]);
hold(axB, "on");
mcsValues = [4 13 18];
seriesColors = [blue; green; purple];
markers = ["o" "s" "^"];
lineStyles = ["-" "--" "-."];
legendHandles = gobjects(numel(mcsValues),1);
for idx = 1:numel(mcsValues)
    curve = sortrows(puschRows(puschRows.mcs_index == mcsValues(idx),:), "snr_db");
    lowerError = curve.initial_bler - curve.initial_bler_wilson95_low;
    upperError = curve.initial_bler_wilson95_high - curve.initial_bler;
    legendHandles(idx) = errorbar(axB, curve.snr_db, curve.initial_bler, ...
        lowerError, upperError, LineStyle=lineStyles(idx), ...
        Marker=markers(idx), Color=seriesColors(idx,:), LineWidth=1.05, ...
        MarkerSize=4.4, MarkerEdgeColor=seriesColors(idx,:), ...
        MarkerFaceColor="white", CapSize=2.5, ...
        DisplayName=sprintf("Scheme index %d", mcsValues(idx)));
    anchorMask = curve.design_role == "precision_anchor";
    if any(anchorMask)
        scatter(axB, curve.snr_db(anchorMask), curve.initial_bler(anchorMask), ...
            35, markers(idx), MarkerEdgeColor=dark, ...
            MarkerFaceColor=seriesColors(idx,:), LineWidth=0.8, ...
            HandleVisibility="off");
    end
end
xlabel(axB, "Signal-to-noise ratio per element (dB)");
ylabel(axB, "Initial uplink block error rate");
xlim(axB, [min(puschRows.snr_db)-0.8 max(puschRows.snr_db)+0.8]);
ylim(axB, [0 1]);
yticks(axB, 0:0.2:1);
grid(axB, "on");
box(axB, "on");
legend(axB, legendHandles, Location="northeast", Box="off", ...
    FontName="Times New Roman", FontSize=6.8);
text(axB, -0.135, 1.07, "(b)", Units="normalized", ...
    FontName="Times New Roman", FontSize=8.2, Color=dark, ...
    HorizontalAlignment="left", VerticalAlignment="bottom");
text(axB, 0.02, 0.965, "Tapped-delay-line C, 300 ns, 30 Hz", Units="normalized", ...
    FontName="Times New Roman", FontSize=6.4, Color=dark, ...
    HorizontalAlignment="left", VerticalAlignment="top");
% Filled precision-anchor markers are explained in the paper's caption and
% text (proof comment 2026-08-12); the former in-figure note is omitted.

set([axA axB], FontName="Times New Roman", FontSize=7.0, ...
    LineWidth=0.72, Layer="top", TickDir="out", GridColor=gridColor, ...
    GridAlpha=0.48, MinorGridAlpha=0.25);
set(axA, XMinorGrid="off", YMinorGrid="off");
set(findall(fig, Type="text"), FontName="Times New Roman");

exportgraphics(fig, pdfPath, ContentType="vector", BackgroundColor="white", ...
    Width=7.16, Height=3.55, Units="inches", Padding="figure", ...
    PreserveAspectRatio="off");
exportgraphics(fig, pngPath, Resolution=450, BackgroundColor="white", ...
    Width=7.16, Height=3.55, Units="inches", Padding="figure", ...
    PreserveAspectRatio="off");

metadata = build_metadata(resolvedSystemCsv, options.PUSCHCsvPath, ...
    pdfPath, pngPath, loads, systemRows, puschRows, ...
    isPreview, previewReasons);
write_json(metadataPath, metadata);

outputs = struct(PDF=string(pdfPath), PNG=string(pngPath), ...
    Metadata=string(metadataPath), PreviewNonfinal=isPreview, ...
    SystemCSV=string(resolvedSystemCsv), PUSCHCSV=options.PUSCHCsvPath);
clear cleanupFigure
close_if_valid(fig);
end

function [rows, resolvedPath] = load_system_rows(inputPath)
resolvedPath = string(inputPath);
if isfolder(resolvedPath)
    resolvedPath = fullfile(resolvedPath, "summary.csv");
end
if ~isfile(resolvedPath)
    error("plot_validation_figure:SystemCSVNotFound", ...
        "System CSV not found: %s", resolvedPath);
end
rows = readtable(resolvedPath, TextType="string");
vars = string(rows.Properties.VariableNames);
requiredArmVars = ["seed", "full_ul_slots", ...
    "cohort_completed_p95_ul_latency_ms", ...
    "cohort_completion_fraction_packets"];
if ~all(ismember(requiredArmVars, vars))
    [folder, name] = fileparts(resolvedPath);
    if name == "paired_differences"
        candidate = fullfile(folder, "summary.csv");
        if ~isfile(candidate)
            error("plot_validation_figure:MissingCompanionSummary", ...
                ["paired_differences.csv contains effects only. Its sibling " ...
                "summary.csv is required for arm-level p95 and completion fractions."]);
        end
        resolvedPath = string(candidate);
        rows = readtable(resolvedPath, TextType="string");
        vars = string(rows.Properties.VariableNames);
    end
end
if ~all(ismember(requiredArmVars, vars))
    missing = requiredArmVars(~ismember(requiredArmVars, vars));
    error("plot_validation_figure:SystemSchema", ...
        "System CSV lacks required arm-level columns: %s", strjoin(missing, ", "));
end
if ~ismember("total_ues", vars)
    if all(ismember(["background_ues", "inference_ues"], vars))
        rows.total_ues = rows.background_ues + rows.inference_ues;
    else
        error("plot_validation_figure:SystemSchema", ...
            "System CSV needs total_ues or background_ues plus inference_ues.");
    end
end
if ~ismember("profile", string(rows.Properties.VariableNames))
    rows.profile = repmat("unknown", height(rows), 1);
end
end

function [rows, loads, previewReasons] = select_and_validate_system_rows(rows, requestedLoads)
previewReasons = strings(0,1);
availableLoads = sort(unique(rows.total_ues))';
if isempty(requestedLoads)
    if all(ismember([208 238], availableLoads))
        loads = [208 238];
    elseif numel(availableLoads) <= 2
        loads = availableLoads;
    else
        error("plot_validation_figure:AmbiguousLoads", ...
            "More than two loads are present; pass TotalUEs=[load1 load2].");
    end
else
    loads = unique(requestedLoads, "stable");
    if numel(loads) > 2
        error("plot_validation_figure:TooManyLoads", ...
            "Panel (a) supports at most two UE loads.");
    end
    if ~all(ismember(loads, availableLoads))
        error("plot_validation_figure:MissingLoad", ...
            "A requested UE load is absent from the system CSV.");
    end
end
rows = rows(ismember(rows.total_ues, loads), :);

numericVars = ["seed", "total_ues", "full_ul_slots", ...
    "cohort_completed_p95_ul_latency_ms", ...
    "cohort_completion_fraction_packets"];
for idx = 1:numel(numericVars)
    value = rows.(numericVars(idx));
    if ~isnumeric(value) || any(~isfinite(value))
        error("plot_validation_figure:SystemValues", ...
            "System column %s must contain finite numeric values.", numericVars(idx));
    end
end
if any(rows.cohort_completed_p95_ul_latency_ms <= 0)
    error("plot_validation_figure:SystemValues", ...
        "Completed-cohort p95 latency must be positive.");
end
if any(rows.cohort_completion_fraction_packets < 0 | ...
        rows.cohort_completion_fraction_packets > 1)
    error("plot_validation_figure:SystemValues", ...
        "Completion fractions must lie in [0,1].");
end
if ismember("cohort_latency_reliable", string(rows.Properties.VariableNames)) && ...
        any(~logical(rows.cohort_latency_reliable))
    error("plot_validation_figure:UnreliableCohort", ...
        "At least one selected completed-cohort latency is marked unreliable.");
end

keys = unique(rows(:, ["total_ues", "seed"]), "rows");
for idx = 1:height(keys)
    mask = rows.total_ues == keys.total_ues(idx) & rows.seed == keys.seed(idx);
    pair = sortrows(rows(mask,:), "full_ul_slots");
    if height(pair) ~= 2 || ~isequal(pair.full_ul_slots(:)', [4 11])
        error("plot_validation_figure:IncompletePair", ...
            "UE load %d seed %d must contain exactly the 4-slot and 11-slot arms.", ...
            keys.total_ues(idx), keys.seed(idx));
    end
    checksumVars = intersect(["traffic_packet_checksum", ...
        "traffic_interval_checksum", "position_checksum", ...
        "link_state_checksum"], string(rows.Properties.VariableNames), ...
        "stable");
    for checksumIdx = 1:numel(checksumVars)
        checksum = pair.(checksumVars(checksumIdx));
        if ~isequaln(checksum(1), checksum(2))
            error("plot_validation_figure:UnpairedTrace", ...
                "UE load %d seed %d differs across arms in %s.", ...
                keys.total_ues(idx), keys.seed(idx), checksumVars(checksumIdx));
        end
    end
end
rows = sortrows(rows, ["total_ues", "seed", "full_ul_slots"]);
end

function validate_pusch_rows(rows)
required = ["profile", "design_role", "mcs_index", "snr_db", ...
    "num_transport_blocks", "initial_bler", ...
    "initial_bler_wilson95_low", "initial_bler_wilson95_high"];
vars = string(rows.Properties.VariableNames);
if ~all(ismember(required, vars))
    missing = required(~ismember(required, vars));
    error("plot_validation_figure:PUSCHSchema", ...
        "PUSCH CSV lacks columns: %s", strjoin(missing, ", "));
end
if ~all(ismember([4 13 18], unique(rows.mcs_index)))
    error("plot_validation_figure:PUSCHMCS", ...
        "PUSCH CSV must contain MCS 4, 13, and 18.");
end
selected = rows(ismember(rows.mcs_index, [4 13 18]),:);
numericVars = ["snr_db", "num_transport_blocks", "initial_bler", ...
    "initial_bler_wilson95_low", "initial_bler_wilson95_high"];
for idx = 1:numel(numericVars)
    value = selected.(numericVars(idx));
    if ~isnumeric(value) || any(~isfinite(value))
        error("plot_validation_figure:PUSCHValues", ...
            "PUSCH column %s must contain finite numeric values.", numericVars(idx));
    end
end
if any(selected.initial_bler < 0 | selected.initial_bler > 1) || ...
        any(selected.initial_bler_wilson95_low < 0 | ...
        selected.initial_bler_wilson95_high > 1) || ...
        any(selected.initial_bler_wilson95_low > selected.initial_bler) || ...
        any(selected.initial_bler_wilson95_high < selected.initial_bler)
    error("plot_validation_figure:PUSCHIntervals", ...
        "BLER values and Wilson intervals are inconsistent.");
end
for mcs = [4 13 18]
    curve = selected(selected.mcs_index == mcs,:);
    if numel(unique(curve.snr_db)) ~= height(curve)
        error("plot_validation_figure:PUSCHDuplicate", ...
            "MCS %d contains duplicate aggregate SNR points.", mcs);
    end
    if ~any(curve.design_role == "precision_anchor")
        error("plot_validation_figure:PUSCHAnchor", ...
            "MCS %d has no precision_anchor row.", mcs);
    end
end
end

function metadata = build_metadata(systemCsv, puschCsv, pdfPath, pngPath, ...
        loads, systemRows, puschRows, isPreview, previewReasons)
metadata = struct();
metadata.generated_utc = char(datetime("now", TimeZone="UTC", ...
    Format="yyyy-MM-dd'T'HH:mm:ss'Z'"));
metadata.preview_nonfinal = isPreview;
metadata.preview_reasons = cellstr(unique(previewReasons, "stable"));
metadata.sources = struct(system_csv=char(systemCsv), pusch_csv=char(puschCsv));
metadata.outputs = struct(pdf=char(pdfPath), png=char(pngPath));
metadata.figure = struct(width_inches=7.16, height_inches=3.55, ...
    png_dpi=450, pdf_content_type="vector", font="Times New Roman", ...
    internal_title_banner=false);
metadata.system = struct();
metadata.system.total_ues = loads;
metadata.system.full_ul_slots = [4 11];
metadata.system.panel_design = "paired performance plane";
metadata.system.x_metric = ...
    "accepted measurement-window cohort completion (%)";
metadata.system.y_metric = "p95 completed-cohort SDU latency (ms)";
metadata.system.metric_scope = ...
    "measurement-generated accepted SDUs completed by finite-drain end";
metadata.system.num_paired_seeds = arrayfun(@(x) numel(unique( ...
    systemRows.seed(systemRows.total_ues == x))), loads);
metadata.system.completion_percent_range = 100 * [ ...
    min(systemRows.cohort_completion_fraction_packets), ...
    max(systemRows.cohort_completion_fraction_packets)];
metadata.system.arm_direction = ...
    "paired path from 4-slot open circle to 11-slot filled diamond";
metadata.system.better_direction = "right and down";
metadata.pusch = struct(mcs_indices=[4 13 18], ...
    metric="initial PUSCH BLER", interval="Wilson 95%", ...
    precision_anchor_transport_blocks=unique(puschRows.num_transport_blocks( ...
    puschRows.design_role == "precision_anchor"))');
end

function write_json(path, value)
fid = fopen(path, "w", "n", "UTF-8");
if fid < 0
    error("plot_validation_figure:MetadataWrite", ...
        "Cannot open metadata path: %s", path);
end
cleanup = onCleanup(@() fclose(fid));
fprintf(fid, "%s\n", jsonencode(value, PrettyPrint=true));
clear cleanup
end

function close_if_valid(fig)
if isgraphics(fig)
    close(fig);
end
end
