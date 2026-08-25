function positions = generate_ue_positions(numUEs, seed, cfg)
%GENERATE_UE_POSITIONS Uniform-area static drops shared by paired TDD arms.

stream = RandStream("mt19937ar", Seed=7000001 + seed);
% rand(2,N) makes the first N positions stable when the population grows.
u = rand(stream, 2, numUEs)';
theta = 2 * pi * u(:,1);
radius = sqrt(cfg.MinRadiusM^2 + ...
    u(:,2) * (cfg.MaxRadiusM^2 - cfg.MinRadiusM^2));
positions = [radius .* cos(theta), radius .* sin(theta), ...
    cfg.UEHeightM * ones(numUEs, 1)];
end
