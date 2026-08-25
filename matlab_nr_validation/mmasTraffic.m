classdef mmasTraffic < wnet.Traffic
    %MMASSTRAFFIC Deterministic replay of a pre-generated mMAS request stream.
    %
    % The constructor draws all inter-arrival times and payload sizes up
    % front.  Recreating the object with the same arguments therefore gives
    % the two TDD arms common random numbers without sharing mutable RNG
    % handles through wnet.Traffic.copy().

    properties (SetAccess = private)
        RateHz (1,1) double
        Seed (1,1) double
        IntervalsMs (:,1) double
        PacketSizes (:,1) double
        GenerationTimesMs (:,1) double
        PacketChecksum (1,1) double
        IntervalChecksum (1,1) double
    end

    properties (Access = private)
        Cursor (1,1) double = 1
    end

    methods
        function obj = mmasTraffic(rateHz, seed, generationStopSeconds)
            obj@wnet.Traffic();
            validateattributes(rateHz, {'numeric'}, ...
                {'scalar', 'real', 'finite', 'positive'});
            validateattributes(seed, {'numeric'}, ...
                {'scalar', 'real', 'finite', 'integer', 'nonnegative'});
            validateattributes(generationStopSeconds, {'numeric'}, ...
                {'scalar', 'real', 'finite', 'positive'});

            obj.RateHz = double(rateHz);
            obj.Seed = double(seed);

            expectedPackets = rateHz * generationStopSeconds;
            % Generous reserve: formal runs need only about 15 background
            % packets per UE, while this allocates at least 128.
            n = max(128, ceil(expectedPackets + ...
                12 * sqrt(max(expectedPackets, 1)) + 64));
            stream = RandStream("mt19937ar", Seed=seed);

            % Draw one extra interval so a one-byte API priming SDU at t=0
            % can be followed by a genuine exponential first-arrival time.
            % This avoids an artificial synchronized full-request burst.
            uArrival = max(rand(stream, n + 1, 1), eps);
            requestIntervalsMs = max(-log(uArrival) / rateHz * 1000, 1e-6);

            sensingBytes = 50000 * rand(stream, n, 1);
            tokenCount = min(exp(5 + 1.5 * randn(stream, n, 1)), 2048);
            requestSizes = max(1, floor(sensingBytes + 4 * tokenCount));

            % nrRLCUM accepts an SDU of at most 9000 bytes. Expand each
            % application request into a back-to-back burst of <=9000-byte
            % SDUs so the modeled request-byte distribution is preserved.
            packetSizes = 1; % API priming SDU; excluded by the warm-up window
            intervalsMs = requestIntervalsMs(1);
            for requestIdx = 1:n
                remaining = requestSizes(requestIdx);
                while remaining > 0
                    chunk = min(remaining, 9000);
                    packetSizes(end+1, 1) = chunk; %#ok<AGROW>
                    remaining = remaining - chunk;
                    if remaining > 0
                        intervalsMs(end+1, 1) = 0.001; %#ok<AGROW> % 1 us
                    else
                        intervalsMs(end+1, 1) = requestIntervalsMs(requestIdx + 1); %#ok<AGROW>
                    end
                end
            end
            generationTimesMs = [0; cumsum(intervalsMs(1:end-1))];
            if generationTimesMs(end) <= generationStopSeconds * 1000
                error("mmasTraffic:SequenceReserve", ...
                    "Pre-generated request reserve does not cover the stop time.");
            end
            keep = generationTimesMs <= generationStopSeconds * 1000;
            obj.PacketSizes = packetSizes(keep);
            obj.IntervalsMs = intervalsMs(keep);
            obj.GenerationTimesMs = generationTimesMs(keep);

            % Simple deterministic audit fingerprints, not cryptographic hashes.
            packetIndex = (1:numel(obj.PacketSizes))';
            obj.PacketChecksum = sum(obj.PacketSizes .* packetIndex);
            obj.IntervalChecksum = sum(round(obj.IntervalsMs * 1000) .* packetIndex);
            % Keep the TrafficManager dormant throughout the drain phase.
            % The sentinel is deliberately excluded from the audit checksum.
            obj.IntervalsMs(end) = 1e12;
        end

        function [dt, packetSize, packet] = generate(obj, ~)
            if obj.Cursor > numel(obj.PacketSizes)
                error("mmasTraffic:SequenceExhausted", ...
                    "Pre-generated traffic exhausted; increase the horizon reserve.");
            end

            idx = obj.Cursor;
            packetSize = obj.PacketSizes(idx);
            dt = obj.IntervalsMs(idx);
            obj.Cursor = idx + 1;

            if nargout >= 3
                packet = zeros(packetSize, 1, "uint8");
            end
        end
    end
end
