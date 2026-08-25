function cfg = nr_validation_config(profile)
%NR_VALIDATION_CONFIG Reproducible presets for the MATLAB NR pilot.

arguments
    profile (1,1) string {mustBeMember(profile, ["smoke", "quick", "medium", "formal"])}
end

cfg = struct();
cfg.Profile = profile;
cfg.CarrierFrequencyHz = 3.5e9;
cfg.ChannelBandwidthHz = 20e6;
cfg.SubcarrierSpacingHz = 30e3;
cfg.NumResourceBlocks = 51;
cfg.NumHARQ = 16;
cfg.MaxNumUsersPerTTI = 4;
cfg.GNBPositionM = [0 0 10];
cfg.UEHeightM = 1.5;
cfg.MinRadiusM = 20;
cfg.MaxRadiusM = 200;
cfg.ArmNames = ["ul20", "ul55"];
cfg.FullULSlots = [4 11];
cfg.TDDPeriodicityMs = 10;
cfg.NumDLSymbolsSpecial = 12;
cfg.NumULSymbolsSpecial = 1;
cfg.ExperimentVersion = "umi-static-shadow-v2";
cfg.ChannelModel = "3GPP-TR38.901-UMi-LOS-NLOS-static-shadowing";
cfg.PHYModel = "abstract-phy";
cfg.Scheduler = "ProportionalFair";
cfg.StaticShadowing = true;
cfg.LOSProbabilityModel = "TR38.901-Table7.4.2-1-UMi-street-canyon";

switch profile
    case "smoke"
        cfg.BackgroundUEs = 2;
        cfg.InferenceUEs = 1;
        cfg.BackgroundRateHz = 5.0;
        cfg.InferenceRateHz = 2.0;
        cfg.WarmupSeconds = 0.10;
        cfg.MeasureSeconds = 0.40;
        cfg.DrainSeconds = 0.50;
        cfg.Seeds = 1;
    case "quick"
        cfg.BackgroundUEs = 8;
        cfg.InferenceUEs = 2;
        cfg.BackgroundRateHz = 2.0;
        cfg.InferenceRateHz = 1.0;
        cfg.WarmupSeconds = 0.50;
        cfg.MeasureSeconds = 2.00;
        cfg.DrainSeconds = 2.00;
        cfg.Seeds = 1;
    case "medium"
        % Runtime-calibration profile: large enough to exercise the
        % high-UE-count scheduler without pretending to be a formal run.
        cfg.BackgroundUEs = 48;
        cfg.InferenceUEs = 4;
        cfg.BackgroundRateHz = 0.50;
        cfg.InferenceRateHz = 0.20;
        cfg.WarmupSeconds = 2.00;
        cfg.MeasureSeconds = 8.00;
        cfg.DrainSeconds = 10.00;
        cfg.Seeds = 1;
    case "formal"
        cfg.BackgroundUEs = [200 230];
        cfg.InferenceUEs = 8;
        cfg.BackgroundRateHz = 0.20;
        cfg.InferenceRateHz = 0.05;
        % Paired finite-window experiment, not a steady-state estimate.
        cfg.WarmupSeconds = 3;
        cfg.MeasureSeconds = 8;
        cfg.DrainSeconds = 10;
        cfg.Seeds = 1:5;
end
end
