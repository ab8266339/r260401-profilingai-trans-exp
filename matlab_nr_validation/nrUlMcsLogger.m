classdef nrUlMcsLogger < handle
    %NRULMCSLOGGER Measurement-window UL MCS distribution from grants.
    %
    % The hidden-but-shipped nrGNB ScheduledResources event exposes the
    % scheduler's ULGrants, including MCSIndex, Type, and PRBSet. Results
    % report both one-sample-per-grant and PRB-weighted distributions.

    properties (SetAccess = private)
        WindowStartSeconds (1,1) double
        WindowEndSeconds (1,1) double
        SlotDurationSeconds (1,1) double
        GrantCounts (:,1) double = zeros(28,1)
        PRBWeightedCounts (:,1) double = zeros(28,1)
        NewGrantCounts (:,1) double = zeros(28,1)
        RetransmissionGrantCounts (:,1) double = zeros(28,1)
        InvalidGrantCount (1,1) double = 0
    end

    methods
        function obj = nrUlMcsLogger(windowStartSeconds, windowEndSeconds, ...
                slotDurationSeconds)
            obj.WindowStartSeconds = windowStartSeconds;
            obj.WindowEndSeconds = windowEndSeconds;
            obj.SlotDurationSeconds = slotDurationSeconds;
        end

        function capture(obj, event)
            data = event.Data;
            if isempty(data.ULGrants)
                return
            end
            grants = data.ULGrants;
            for idx = 1:numel(grants)
                transmissionTime = data.CurrentTime + ...
                    double(grants(idx).SlotOffset) * obj.SlotDurationSeconds;
                if transmissionTime <= obj.WindowStartSeconds || ...
                        transmissionTime > obj.WindowEndSeconds
                    continue
                end
                mcs = double(grants(idx).MCSIndex);
                if ~isscalar(mcs) || ~isfinite(mcs) || ...
                        mcs < 0 || mcs > 27 || mcs ~= floor(mcs)
                    obj.InvalidGrantCount = obj.InvalidGrantCount + 1;
                    continue
                end
                bin = mcs + 1;
                prbWeight = numel(grants(idx).PRBSet);
                obj.GrantCounts(bin) = obj.GrantCounts(bin) + 1;
                obj.PRBWeightedCounts(bin) = ...
                    obj.PRBWeightedCounts(bin) + prbWeight;
                switch string(grants(idx).Type)
                    case "newTx"
                        obj.NewGrantCounts(bin) = obj.NewGrantCounts(bin) + 1;
                    case "reTx"
                        obj.RetransmissionGrantCounts(bin) = ...
                            obj.RetransmissionGrantCounts(bin) + 1;
                    otherwise
                        obj.InvalidGrantCount = obj.InvalidGrantCount + 1;
                end
            end
        end

        function [result, histogram] = summarize(obj)
            indices = (0:27)';
            totalGrants = sum(obj.GrantCounts);
            totalPRBs = sum(obj.PRBWeightedCounts);
            result = struct();
            result.GrantCount = totalGrants;
            result.NewGrantCount = sum(obj.NewGrantCounts);
            result.RetransmissionGrantCount = ...
                sum(obj.RetransmissionGrantCounts);
            result.InvalidGrantCount = obj.InvalidGrantCount;
            result.MeanGrantWeighted = weighted_mean(indices, obj.GrantCounts);
            result.P50GrantWeighted = weighted_percentile( ...
                indices, obj.GrantCounts, 0.50);
            result.P95GrantWeighted = weighted_percentile( ...
                indices, obj.GrantCounts, 0.95);
            result.MeanPRBWeighted = weighted_mean( ...
                indices, obj.PRBWeightedCounts);
            result.P50PRBWeighted = weighted_percentile( ...
                indices, obj.PRBWeightedCounts, 0.50);
            result.P95PRBWeighted = weighted_percentile( ...
                indices, obj.PRBWeightedCounts, 0.95);
            result.TotalScheduledPRBs = totalPRBs;
            result.Reliable = totalGrants > 0 && obj.InvalidGrantCount == 0;

            histogram = table(indices, obj.GrantCounts, ...
                obj.PRBWeightedCounts, obj.NewGrantCounts, ...
                obj.RetransmissionGrantCounts, ...
                VariableNames=["mcs_index", "grant_count", ...
                "scheduled_prb_count", "new_tx_grant_count", ...
                "retx_grant_count"]);
        end
    end
end

function value = weighted_mean(values, weights)
total = sum(weights);
if total > 0
    value = sum(values .* weights) / total;
else
    value = NaN;
end
end

function value = weighted_percentile(values, weights, probability)
total = sum(weights);
if total <= 0
    value = NaN;
    return
end
threshold = probability * total;
index = find(cumsum(weights) >= threshold, 1);
value = values(index);
end
