package com.example.demo.routing;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import static org.assertj.core.api.Assertions.*;

class SessionRoutingGateTests {
    @TempDir Path directory;
    final ObjectMapper mapper = new ObjectMapper();

    @Test void zeroErrorsStillNeeds2995IndependentSamples() {
        assertThat(SessionRoutingGate.upperBound(0, 0)).isEqualTo(1);
        assertThat(SessionRoutingGate.upperBound(2994, 0)).isGreaterThan(.001);
        assertThat(SessionRoutingGate.upperBound(2995, 0)).isLessThanOrEqualTo(.001);
        assertThat(SessionRoutingGate.upperBound(2995, 1)).isGreaterThan(.001);
    }

    @Test void missingOrForgedSummaryNeverOpensGate() throws Exception {
        Path report = directory.resolve("report.json");
        var gate = new SessionRoutingGate(mapper, report.toString(), "jev-1.14.0");
        assertThat(gate.accepted()).isFalse();
        for (int n : new int[] {100, 2995}) {
            Files.writeString(report, mapper.writeValueAsString(Map.of("gate", Map.of(
                    "passed", true, "upper_bound", 0.0, "risk_limit", .001, "confidence_level", .95,
                    "n", n, "errors", 0, "taxonomy_version", "intent-routing.v1",
                    "model_version", "jev-1.14.0", "policy_fingerprint", "frozen-policy"))));
            assertThat(gate.accepted()).isEqualTo(n == 2995);
        }
        assertThat(new SessionRoutingGate(mapper, report.toString(), "different-model").accepted()).isFalse();
    }
}
