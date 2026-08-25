function report = verify_validation_figure(outputDirectory, options)
%VERIFY_VALIDATION_FIGURE Verify publication-figure files and export settings.

arguments
    outputDirectory (1,1) string
    options.BaseName (1,1) string = ""
end

if ~isfolder(outputDirectory)
    error("verify_validation_figure:MissingDirectory", ...
        "Output directory not found: %s", outputDirectory);
end
if strlength(options.BaseName) == 0
    metadataFiles = dir(fullfile(outputDirectory, "*_metadata.json"));
    if numel(metadataFiles) ~= 1
        error("verify_validation_figure:AmbiguousMetadata", ...
            "Pass BaseName when the output directory has zero or multiple metadata files.");
    end
    metadataPath = fullfile(metadataFiles(1).folder, metadataFiles(1).name);
else
    metadataPath = fullfile(outputDirectory, options.BaseName + "_metadata.json");
end
if ~isfile(metadataPath)
    error("verify_validation_figure:MissingMetadata", ...
        "Metadata file not found: %s", metadataPath);
end

metadata = jsondecode(fileread(metadataPath));
pdfPath = string(metadata.outputs.pdf);
pngPath = string(metadata.outputs.png);
assert(isfile(pdfPath), "Vector PDF is missing: %s", pdfPath);
assert(isfile(pngPath), "450 dpi PNG is missing: %s", pngPath);
assert(isfile(string(metadata.sources.system_csv)), "System source CSV is missing.");
assert(isfile(string(metadata.sources.pusch_csv)), "PUSCH source CSV is missing.");

fid = fopen(pdfPath, "r");
assert(fid >= 0, "Cannot read PDF: %s", pdfPath);
cleanup = onCleanup(@() fclose(fid));
pdfBytes = fread(fid, Inf, "*uint8");
clear cleanup
assert(numel(pdfBytes) > 1000, "PDF is unexpectedly small.");
assert(strcmp(char(pdfBytes(1:5))', "%PDF-"), "Output does not have a PDF header.");
pdfText = string(char(pdfBytes'));
imageXObjects = count(pdfText, "/Subtype /Image");
assert(imageXObjects == 0, ...
    "PDF contains %d raster image XObject(s); expected vector-only chart content.", ...
    imageXObjects);
assert(contains(pdfText, "TimesNewRoman"), ...
    "Times New Roman was not found in the exported PDF font resources.");

pngInfo = imfinfo(pngPath);
assert(pngInfo.Width >= 3200, ...
    "PNG width %d px is below expected 7.16 in x 450 dpi output.", pngInfo.Width);
assert(pngInfo.Height >= 1500, ...
    "PNG height %d px is below expected publication preview height.", pngInfo.Height);
dpiX = resolution_to_dpi(pngInfo.XResolution, pngInfo.ResolutionUnit);
dpiY = resolution_to_dpi(pngInfo.YResolution, pngInfo.ResolutionUnit);
assert(abs(dpiX - 450) <= 1 && abs(dpiY - 450) <= 1, ...
    "PNG resolution is %.2f x %.2f dpi, expected 450 dpi.", dpiX, dpiY);

assert(strcmp(metadata.figure.font, "Times New Roman"), ...
    "Metadata does not declare Times New Roman.");
assert(metadata.figure.internal_title_banner == false, ...
    "Figure metadata indicates an internal title banner.");
assert(strcmp(metadata.system.panel_design, "paired performance plane"), ...
    "System panel is not declared as a paired performance plane.");
assert(contains(string(metadata.system.x_metric), "completion"), ...
    "System-panel x metric does not expose cohort completion.");
assert(contains(string(metadata.system.y_metric), "p95"), ...
    "System-panel y metric does not expose completed-cohort p95 latency.");
assert(strcmp(metadata.system.arm_direction, ...
    "paired path from 4-slot open circle to 11-slot filled diamond"), ...
    "System-panel arm encoding metadata is missing or inconsistent.");
assert(strcmp(metadata.system.better_direction, "right and down"), ...
    "System-panel performance direction metadata is inconsistent.");
if ~logical(metadata.preview_nonfinal)
    pairedSeeds = double(metadata.system.num_paired_seeds(:)');
    assert(isequal(pairedSeeds, [5 5]), ...
        "A formal figure requires exactly five paired seeds at each load.");
end

report = struct(PDF=pdfPath, PNG=pngPath, Metadata=string(metadataPath), ...
    VectorPDFNoImageXObjects=true, PNGWidth=pngInfo.Width, ...
    PNGHeight=pngInfo.Height, PNGDPI=[dpiX dpiY], ...
    PreviewNonfinal=logical(metadata.preview_nonfinal));
disp(report);
end

function dpi = resolution_to_dpi(resolution, unit)
switch lower(string(unit))
    case {"inch", "inches"}
        dpi = double(resolution);
    case {"centimeter", "centimeters", "cm"}
        dpi = double(resolution) * 2.54;
    case {"meter", "meters", "m"}
        dpi = double(resolution) / 39.3700787401575;
    otherwise
        error("verify_validation_figure:ResolutionUnit", ...
            "Unsupported PNG resolution unit: %s", unit);
end
end
