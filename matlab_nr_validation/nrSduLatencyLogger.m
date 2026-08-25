classdef nrSduLatencyLogger < handle
    %NRSDULATENCYLOGGER Exact receive-window SDU latency samples.
    %
    % AppPacketReceived is emitted after the TrafficManager updates its
    % cumulative latency counter. The difference between successive totals
    % is therefore the latency of the just-received SDU. This avoids trying
    % to recover a percentile from an aggregate mean and remains valid when
    % packets are dropped before reception.

    properties (SetAccess = private)
        TrafficManager
        ReceiptTimesSeconds (:,1) double = zeros(0,1)
        GenerationTimesSeconds (:,1) double = zeros(0,1)
        PacketLengthsBytes (:,1) double = zeros(0,1)
        LatenciesMs (:,1) double = zeros(0,1)
        LastTotalLatencySeconds (1,1) double = 0
        LastReceivedPackets (1,1) double = 0
        CallbackCountMismatch (1,1) double = 0
    end

    methods
        function obj = nrSduLatencyLogger(trafficManager)
            obj.TrafficManager = trafficManager;
        end

        function capture(obj, event)
            stats = statistics(obj.TrafficManager, true);
            if isempty(stats.Destinations)
                currentTotalLatency = 0;
            else
                currentTotalLatency = sum([stats.Destinations.TotalPacketLatency]);
            end
            currentReceivedPackets = stats.ReceivedPackets;
            if currentReceivedPackets ~= obj.LastReceivedPackets + 1
                obj.CallbackCountMismatch = obj.CallbackCountMismatch + 1;
            end

            latencySeconds = currentTotalLatency - obj.LastTotalLatencySeconds;
            obj.ReceiptTimesSeconds(end+1,1) = event.Timestamp;
            obj.GenerationTimesSeconds(end+1,1) = ...
                event.Timestamp - latencySeconds;
            obj.PacketLengthsBytes(end+1,1) = event.EventData.PacketLength;
            obj.LatenciesMs(end+1,1) = latencySeconds * 1000;
            obj.LastTotalLatencySeconds = currentTotalLatency;
            obj.LastReceivedPackets = currentReceivedPackets;
        end

        function result = summarize(obj, windowStart, windowEnd, ...
                expectedCount, expectedMeanMs)
            % Events at exactly windowStart can belong to the preceding
            % simulator step. Try the strict boundary first, then accept
            % the closed boundary only if it matches the counter delta.
            strictMask = obj.ReceiptTimesSeconds > windowStart & ...
                obj.ReceiptTimesSeconds <= windowEnd;
            closedMask = obj.ReceiptTimesSeconds >= windowStart & ...
                obj.ReceiptTimesSeconds <= windowEnd;
            if sum(strictMask) == expectedCount
                mask = strictMask;
                boundary = "(start,end]";
            elseif sum(closedMask) == expectedCount
                mask = closedMask;
                boundary = "[start,end]";
            else
                mask = strictMask;
                boundary = "unmatched";
            end

            samples = obj.LatenciesMs(mask);
            if isempty(samples)
                observedMean = NaN;
                p95 = NaN;
            else
                observedMean = mean(samples);
                p95 = linear_percentile(samples, 0.95);
            end
            meanError = abs(observedMean - expectedMeanMs);
            reliable = numel(samples) == expectedCount && ...
                obj.CallbackCountMismatch == 0 && ...
                (expectedCount == 0 || meanError <= 1e-6);
            if ~reliable
                p95 = NaN;
            end

            result = struct();
            result.P95LatencyMs = p95;
            result.SampleCount = numel(samples);
            result.Reliable = reliable;
            result.ObservedMeanMs = observedMean;
            result.MeanConsistencyErrorMs = meanError;
            result.WindowBoundary = boundary;
            result.CallbackCountMismatch = obj.CallbackCountMismatch;
        end

        function result = summarizeCohort(obj, generationStart, ...
                generationEnd, drainEnd, acceptedPackets, acceptedBytes)
            mask = obj.GenerationTimesSeconds > generationStart & ...
                obj.GenerationTimesSeconds <= generationEnd & ...
                obj.ReceiptTimesSeconds <= drainEnd;
            samples = obj.LatenciesMs(mask);
            completedBytes = sum(obj.PacketLengthsBytes(mask));
            completedPackets = numel(samples);
            if completedPackets > 0
                completedMean = mean(samples);
                completedP95 = linear_percentile(samples, 0.95);
            else
                completedMean = NaN;
                completedP95 = NaN;
            end
            reliable = obj.CallbackCountMismatch == 0 && ...
                completedPackets <= acceptedPackets && ...
                completedBytes <= acceptedBytes && all(samples >= 0);
            if ~reliable
                completedMean = NaN;
                completedP95 = NaN;
            end

            result = struct();
            result.CompletedPackets = completedPackets;
            result.CompletedBytes = completedBytes;
            result.UnfinishedOrLostPackets = acceptedPackets - completedPackets;
            result.UnfinishedOrLostBytes = acceptedBytes - completedBytes;
            result.CompletionFractionPackets = safe_ratio( ...
                completedPackets, acceptedPackets);
            result.CompletionFractionBytes = safe_ratio( ...
                completedBytes, acceptedBytes);
            result.CompletedMeanLatencyMs = completedMean;
            result.CompletedP95LatencyMs = completedP95;
            result.CompletedAfterGenerationWindow = sum(mask & ...
                obj.ReceiptTimesSeconds > generationEnd);
            result.Reliable = reliable;
        end
    end
end

function value = linear_percentile(samples, probability)
% Hyndman-Fan type 7, matching the common linear sample quantile.
ordered = sort(samples(:));
n = numel(ordered);
if n == 1
    value = ordered(1);
    return
end
position = 1 + (n - 1) * probability;
lower = floor(position);
upper = ceil(position);
fraction = position - lower;
value = ordered(lower) + fraction * (ordered(upper) - ordered(lower));
end

function value = safe_ratio(numerator, denominator)
if denominator > 0
    value = numerator / denominator;
else
    value = NaN;
end
end
