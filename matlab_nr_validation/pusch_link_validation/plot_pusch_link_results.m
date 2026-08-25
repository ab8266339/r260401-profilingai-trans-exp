function paths = plot_pusch_link_results(summary,cfg,root)
%PLOT_PUSCH_LINK_RESULTS Export a compact paper-grade two-panel figure.

pdfDir = fullfile(root,"output","pdf");
pngDir = fullfile(root,"output","png");
if ~isfolder(pdfDir), mkdir(pdfDir); end
if ~isfolder(pngDir), mkdir(pngDir); end
if cfg.Profile == "formal"
    stem = "fig_pusch_link_calibration";
else
    stem = "fig_pusch_link_calibration_smoke";
end
pdfPath = fullfile(pdfDir,stem + ".pdf");
pngPath = fullfile(pngDir,stem + ".png");

fig = figure(Visible="off",Color="white",Units="inches",Position=[1 1 11.2 4.25]);
layout = tiledlayout(fig,1,2,TileSpacing="compact",Padding="compact");
colors = [0.0000 0.4470 0.7410; 0.8500 0.3250 0.0980; 0.4660 0.6740 0.1880];
markers = ["o","s","^"];

ax1 = nexttile(layout,1);
hold(ax1,"on");
for idx = 1:numel(cfg.MCS)
    mask = summary.mcs_index == cfg.MCS(idx).Index;
    curve = summary(mask,:);
    floorValue = 0.5 ./ curve.num_transport_blocks;
    plottedBLER = max(curve.initial_bler,floorValue);
    lowerBound = max(curve.initial_bler_wilson95_low,1e-3);
    upperBound = max(curve.initial_bler_wilson95_high,plottedBLER);
    errorbar(ax1,curve.snr_db,plottedBLER, ...
        plottedBLER-lowerBound,upperBound-plottedBLER,"-"+markers(idx), ...
        Color=colors(idx,:),LineWidth=1.7,MarkerSize=6, ...
        MarkerFaceColor="white",DisplayName=sprintf("MCS %d: %s, R=%.3f", ...
        cfg.MCS(idx).Index,cfg.MCS(idx).Modulation,cfg.MCS(idx).TargetCodeRate));
    anchorMask = curve.design_role == "precision_anchor";
    if any(anchorMask)
        plot(ax1,curve.snr_db(anchorMask),plottedBLER(anchorMask), ...
            LineStyle="none",Marker=markers(idx),MarkerSize=7, ...
            MarkerEdgeColor=colors(idx,:),MarkerFaceColor=colors(idx,:), ...
            HandleVisibility="off");
    end
end
grid(ax1,"on"); box(ax1,"on");
xlabel(ax1,"SNR per resource element (dB)");
ylabel(ax1,"Initial transmission BLER");
title(ax1,"(a) Practical DM-RS receiver");
ylim(ax1,[1e-3 1]);
set(ax1,YScale="log",YTick=[1e-3 1e-2 1e-1 1]);
legend(ax1,Location="southwest",FontSize=8);

ax2 = nexttile(layout,2);
hold(ax2,"on");
for idx = 1:numel(cfg.MCS)
    mask = summary.mcs_index == cfg.MCS(idx).Index;
    curve = summary(mask,:);
    plot(ax2,curve.snr_db,curve.supported_full_ul_rate_mbps,"-"+markers(idx), ...
        Color=colors(idx,:),LineWidth=1.7,MarkerSize=6,MarkerFaceColor="white", ...
        DisplayName=sprintf("MCS %d",cfg.MCS(idx).Index));
    anchorMask = curve.design_role == "precision_anchor";
    if any(anchorMask)
        plot(ax2,curve.snr_db(anchorMask), ...
            curve.supported_full_ul_rate_mbps(anchorMask), ...
            LineStyle="none",Marker=markers(idx),MarkerSize=7, ...
            MarkerEdgeColor=colors(idx,:),MarkerFaceColor=colors(idx,:), ...
            HandleVisibility="off");
    end
end
grid(ax2,"on"); box(ax2,"on");
xlabel(ax2,"SNR per resource element (dB)");
ylabel(ax2,"Supported full-UL rate (Mbit/s)");
title(ax2,"(b) HARQ-aware delivered rate");
legend(ax2,Location="southeast",FontSize=8);

title(layout,sprintf("PUSCH link-level cross-check: 51 RB, 30 kHz SCS, TDL-C 300 ns / 30 Hz"), ...
    FontWeight="bold",FontSize=11);
subtitle(layout,"Wilson 95% bars; filled markers: N=1000 anchors; open markers: N=180; zero errors shown at 0.5/N.", ...
    FontSize=8);
allAxes = findall(fig,Type="axes");
set(allAxes,FontName="Times New Roman",FontSize=9,LineWidth=0.8,Layer="top");

exportgraphics(fig,pdfPath,ContentType="vector",BackgroundColor="white");
exportgraphics(fig,pngPath,Resolution=450,BackgroundColor="white");
close(fig);
paths = struct(PDF=string(pdfPath),PNG=string(pngPath));
end
