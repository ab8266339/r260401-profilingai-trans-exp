function linkState = generate_umi_link_state(positions, seed, cfg)
%GENERATE_UMI_LINK_STATE Auditable static TR 38.901 UMi link realization.
%
% LOS/NLOS state and shadowing are drawn once per UE and then reused by
% both paired TDD arms. Separate streams keep the first N links invariant
% when a larger background-UE population is selected with the same seed.

arguments
    positions (:,3) double
    seed (1,1) double {mustBeInteger, mustBePositive}
    cfg (1,1) struct
end

numUEs = size(positions, 1);
delta = positions(:, 1:2) - cfg.GNBPositionM(1:2);
distance2D = sqrt(sum(delta.^2, 2));

% 3GPP TR 38.901, Table 7.4.2-1, UMi street-canyon LOS probability.
losProbability = min(18 ./ distance2D, 1) .* ...
    (1 - exp(-distance2D / 36)) + exp(-distance2D / 36);

losStream = RandStream("mt19937ar", Seed=8000001 + seed);
shadowStream = RandStream("mt19937ar", Seed=9000001 + seed);
losUniform = rand(losStream, numUEs, 1);
isLOS = losUniform <= losProbability;
shadowStandardNormal = randn(shadowStream, numUEs, 1);

pathLossConfig = nrPathLossConfig(Scenario="UMi");
basePathLossDb = zeros(numUEs, 1);
shadowSigmaDb = zeros(numUEs, 1);
for idx = 1:numUEs
    [basePathLossDb(idx), shadowSigmaDb(idx)] = nrPathLoss( ...
        pathLossConfig, cfg.CarrierFrequencyHz, isLOS(idx), ...
        cfg.GNBPositionM(:), positions(idx, :)');
end

if cfg.StaticShadowing
    shadowDb = shadowSigmaDb .* shadowStandardNormal;
else
    shadowDb = zeros(numUEs, 1);
end
totalPathLossDb = basePathLossDb + shadowDb;

positionKeys = strings(numUEs, 1);
for idx = 1:numUEs
    positionKeys(idx) = position_key(positions(idx, :));
end
assert(numel(unique(positionKeys)) == numUEs, ...
    "UE position keys must be unique.");
keyToIndex = containers.Map(cellstr(positionKeys), num2cell(1:numUEs));

linkIndex = (1:numUEs)';
checksumTerms = round(totalPathLossDb * 1000) + ...
    1000000 * double(isLOS) + round(losProbability * 100000);
linkChecksum = sum(checksumTerms .* linkIndex);

linkState = struct();
linkState.Positions = positions;
linkState.PositionKeys = positionKeys;
linkState.KeyToIndex = keyToIndex;
linkState.Distance2DM = distance2D;
linkState.LOSProbability = losProbability;
linkState.LOSUniform = losUniform;
linkState.IsLOS = isLOS;
linkState.BasePathLossDb = basePathLossDb;
linkState.ShadowSigmaDb = shadowSigmaDb;
linkState.ShadowStandardNormal = shadowStandardNormal;
linkState.ShadowDb = shadowDb;
linkState.TotalPathLossDb = totalPathLossDb;
linkState.Checksum = linkChecksum;
end

function key = position_key(position)
% Six decimals are finer than a micrometre for the metre-valued positions
% used here and preserve the exact static node coordinates in callbacks.
key = sprintf("%.6f|%.6f|%.6f", position(1), position(2), position(3));
end
