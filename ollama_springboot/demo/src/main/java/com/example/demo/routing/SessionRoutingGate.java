package com.example.demo.routing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.file.Files;
import java.nio.file.Path;

/** A release latch is insufficient without a versioned statistical acceptance report. */
@Component
public class SessionRoutingGate {
    private static final Logger logger = LoggerFactory.getLogger(SessionRoutingGate.class);
    private final ObjectMapper mapper;
    private final String reportPath;
    private final String modelVersion;

    public SessionRoutingGate(ObjectMapper mapper,
            @Value("${app.routing.gate-report:}") String reportPath,
            @Value("${app.routing.model-version:jev-1.14.0}") String modelVersion) {
        this.mapper = mapper;
        this.reportPath = reportPath;
        this.modelVersion = modelVersion;
    }

    public boolean accepted() {
        if (reportPath == null || reportPath.isBlank()) return false;
        try {
            JsonNode gate = mapper.readTree(Files.readString(Path.of(reportPath))).path("gate");
            int n = gate.path("n").asInt(-1), errors = gate.path("errors").asInt(-1);
            return gate.path("passed").asBoolean(false)
                    && "intent-routing.v1".equals(gate.path("taxonomy_version").asText())
                    && modelVersion.equals(gate.path("model_version").asText())
                    && !gate.path("policy_fingerprint").asText().isBlank()
                    && Math.abs(gate.path("confidence_level").asDouble() - .95) < 1e-9
                    && Math.abs(gate.path("risk_limit").asDouble() - .001) < 1e-9
                    && n > 0 && errors >= 0 && errors <= n
                    && upperBound(n, errors) <= .001;
        } catch (Exception invalid) {
            logger.warn("routing.gate.invalid error_type={}", invalid.getClass().getSimpleName());
            return false;
        }
    }

    static double upperBound(int n, int errors) {
        if (n <= 0 || errors < 0 || errors >= n) return 1.0;
        if (errors == 0) return -Math.expm1(Math.log(.05) / n);
        double low = (double) errors / n, high = 1.0;
        for (int step = 0; step < 70; step++) {
            double p = (low + high) / 2;
            double logTerm = n * Math.log1p(-p), logSum = logTerm;
            for (int k = 1; k <= errors; k++) {
                logTerm += Math.log(n - k + 1.0) - Math.log(k) + Math.log(p) - Math.log1p(-p);
                double max = Math.max(logSum, logTerm);
                logSum = max + Math.log(Math.exp(logSum - max) + Math.exp(logTerm - max));
            }
            if (logSum > Math.log(.05)) low = p;
            else high = p;
        }
        return high;
    }
}
