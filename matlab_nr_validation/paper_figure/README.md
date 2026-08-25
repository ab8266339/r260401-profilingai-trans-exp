# MATLAB R2026a paper figure

This isolated plotting utility combines two evidence layers without changing
the simulation sources or manuscript:

- panel (a): a paired, seed-level performance plane whose x-axis is accepted
  measurement-window cohort completion and whose y-axis is p95 latency for
  **completed-cohort SDUs**. Each paired path runs from the 4-slot open circle to the
  11-slot filled diamond, so an improvement appears directly as rightward and
  downward movement; color separates the 208- and 238-UE loads;
- panel (b): initial PUSCH BLER for MCS 4/13/18 with Wilson 95% intervals.
  Open markers are ordinary curve points and filled, dark-edged markers are the
  higher-precision anchors.

The figure is 7.16 inches wide (IEEE double-column width), uses Times New
Roman, has no internal title banner or dual y-axis, and exports a vector PDF
plus a 450 dpi PNG. Color is backed by marker shapes and line styles.

## Inputs and claim boundary

`plot_validation_figure` accepts an aggregate `summary.csv`, a current two-arm
pair CSV, or `paired_differences.csv` as its first argument. When a delta-only
`paired_differences.csv` is supplied, its sibling `summary.csv` is resolved
automatically; the delta file alone cannot recover the two arm-specific points
on the completion/latency plane.

The PUSCH input defaults to the existing formal file:

```
../pusch_link_validation/results/formal/summary.csv
```

A figure is considered final only when the system input is formal, contains
both 208 and 238 total UEs, has exactly five complete paired seeds per load,
and the PUSCH input is formal. Otherwise the output stem and panel annotation
say `preview_nonfinal`. The utility never substitutes or synthesizes missing
loads, seeds, latency values, completion fractions, or BLER points.

## Run a layout preview

From PowerShell:

```powershell
./run_r2026a_figure.ps1 `
  -SystemCsv "../results/aggregates/umi-static-shadow-v2/smoke/summary.csv" `
  -Preview
```

The smoke aggregate is for plumbing/layout only and must not be cited as paper
evidence.

## Run the final figure

After the formal aggregate has completed:

```powershell
./run_r2026a_figure.ps1 `
  -SystemCsv "../results/aggregates/umi-static-shadow-v2/formal/summary.csv"
```

Optional parameters are `-PUSCHCsv`, `-OutputDirectory`, `-BaseName`, and
`-TotalUEs "208,238"`. The wrapper deliberately locates the registered MATLAB
R2026a executable instead of using the system `matlab` alias.

## Verify

Every run invokes `verify_validation_figure`. It checks source provenance,
PDF/PNG existence, a valid PDF header, absence of raster image XObjects in the
vector PDF, 450 dpi PNG metadata and expected pixel dimensions, Times New Roman
resources, no title banner, and the performance-plane axes, direction, and arm
encoding recorded in the provenance metadata.

For a separate verification call in MATLAB R2026a:

```matlab
verify_validation_figure("output", ...
    BaseName="fig_nr_validation_preview_nonfinal")
```
