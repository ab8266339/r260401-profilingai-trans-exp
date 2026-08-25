function cfg = pusch_link_config(profile)
%PUSCH_LINK_CONFIG Auditable presets for the PUSCH link-level cross-check.

arguments
    profile (1,1) string {mustBeMember(profile,["smoke","formal"])}
end

cfg = struct();
cfg.Profile = profile;
cfg.Release = "2026a";
cfg.CarrierFrequencyHz = 3.5e9;
cfg.NominalBandwidthHz = 20e6;
cfg.SubcarrierSpacingKHz = 30;
cfg.NSizeGrid = 51;
cfg.OccupiedBandwidthHz = cfg.NSizeGrid * 12 * cfg.SubcarrierSpacingKHz * 1e3;
cfg.NCellID = 1;
cfg.RNTI = 1;
cfg.NumTxAntennas = 1;
cfg.NumRxAntennas = 1;
cfg.ChannelProfile = "TDL-C";
cfg.DelaySpreadSeconds = 300e-9;
cfg.MaximumDopplerHz = 30;
cfg.ChannelSeedOffset = 260401;
cfg.RVSequence = [0 2 3 1];
cfg.MaximumLDPCIterations = 8;
cfg.LDPCAlgorithm = "Normalized min-sum";
cfg.Estimator = "PUSCH DM-RS practical timing and channel estimation";
cfg.SNRDefinition = "per resource element and receive antenna";
cfg.AWGNSanitySNRdB = 30;
cfg.AWGNSanityTBPerMCS = 3;
cfg.PointRole = "curve_point";
cfg.AnchorPoints = repmat(struct("MCSIndex",0,"SNRdB",0),0,1);
cfg.AnchorSeeds = [];
cfg.AnchorTBPerSeed = 0;

% MCS table 1 values from 3GPP TS 38.214, Table 5.1.3.1-1.
cfg.MCS = repmat(struct( ...
    "Index",0,"Modulation","QPSK","TargetCodeRate",0,"SNRdB",[]),3,1);
cfg.MCS(1) = struct("Index",4,"Modulation","QPSK", ...
    "TargetCodeRate",308/1024,"SNRdB",[]);
cfg.MCS(2) = struct("Index",13,"Modulation","16QAM", ...
    "TargetCodeRate",490/1024,"SNRdB",[]);
cfg.MCS(3) = struct("Index",18,"Modulation","64QAM", ...
    "TargetCodeRate",466/1024,"SNRdB",[]);

switch profile
    case "smoke"
        cfg.Seeds = 1;
        cfg.TBPerSeed = 2;
        cfg.MCS(1).SNRdB = [-2 2];
        cfg.MCS(2).SNRdB = [4 8];
        cfg.MCS(3).SNRdB = [10 14];
    case "formal"
        cfg.Seeds = 1:3;
        cfg.TBPerSeed = 60;
        cfg.AnchorSeeds = 1:5;
        cfg.AnchorTBPerSeed = 200;
        cfg.AnchorPoints = repmat(struct("MCSIndex",0,"SNRdB",0),3,1);
        cfg.AnchorPoints(1) = struct("MCSIndex",4,"SNRdB",6);
        cfg.AnchorPoints(2) = struct("MCSIndex",13,"SNRdB",15);
        cfg.AnchorPoints(3) = struct("MCSIndex",18,"SNRdB",20);
        cfg.MCS(1).SNRdB = [4 5 6 7 8 9];
        cfg.MCS(2).SNRdB = [12 13 14 15 16 17];
        cfg.MCS(3).SNRdB = [14 16 18 20 22 24];
end
end
