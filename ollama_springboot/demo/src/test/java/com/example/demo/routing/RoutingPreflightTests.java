package com.example.demo.routing;

import com.example.demo.auth.BusinessAccess;
import com.example.demo.client.FastApiClient;
import com.example.demo.conversation.entity.ConversationEntity;
import com.example.demo.conversation.service.ConversationService;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class RoutingPreflightTests {
    final BusinessAccess access = mock(BusinessAccess.class);
    final ConversationService conversations = mock(ConversationService.class);
    final FastApiClient agent = mock(FastApiClient.class);
    final SessionRoutingGate gate = mock(SessionRoutingGate.class);

    @Test void rolloutUsesSharedStableSha256Bucket() {
        assertThat(RoutingPreflight.cohortBucket("owner-1:session-1")).isEqualTo(3);
        assertThat(RoutingPreflight.cohortBucket("owner-1:new")).isEqualTo(9);
        candidate("current");
        var service = new RoutingPreflight(access, conversations, agent, gate, "shadow", false, 0);
        service.resolve("owner", "continue", false, "auto", null, null, null, "current", 60);
        verify(agent).shadowRouting(argThat(snapshot -> "owner:current".equals(snapshot.get("cohort_key"))));
    }

    ConversationEntity candidate(String id) {
        ConversationEntity result = new ConversationEntity(); result.setId(id); result.setTitle("Current topic");
        when(access.conversation("owner", id)).thenReturn(result);
        when(conversations.findActiveConversation(id)).thenReturn(result);
        when(conversations.getFacts(id)).thenReturn(Map.of("language", "Chinese"));
        when(conversations.getRecentMessages(id, 6)).thenReturn(List.of());
        return result;
    }

    @Test void shadowNeverUsesPredictionOrReturnsItToUser() {
        candidate("current");
        var service = new RoutingPreflight(access, conversations, agent, gate, "shadow", false, 0);
        var result = service.resolve("owner", "continue", false, "auto", null, null, null, "current", 60);
        assertThat(result.needsClarification()).isTrue();
        assertThat(result.decision()).isNull();
        verify(agent).shadowRouting(argThat(snapshot -> "unresolved".equals(snapshot.get("binding"))));
        verify(agent, never()).resolveRouting(any());
        verify(conversations, never()).ensureOwnedConversation(any(), any(), any(), any());
    }

    @Test void strongTargetOverridesAutoAndIgnoresModelTargetChange() {
        candidate("current");
        when(access.matchingConversation(eq("owner"), eq("current"), isNull())).thenReturn("current");
        when(agent.resolveRouting(any())).thenReturn(Map.of("schema_version", "intent-routing.v1", "status", "ready",
                "target", "new", "mode", "direct"));
        var service = new RoutingPreflight(access, conversations, agent, gate, "live", true, 100);
        var result = service.resolve("owner", "hello", false, "auto", "current", null, null, null, 60);
        assertThat(result.conversationId()).isEqualTo("current");
        verify(agent).resolveRouting(argThat(snapshot -> "existing".equals(snapshot.get("binding"))));
    }

    @Test void automaticAssignmentRequiresStatisticalGateAndAllowedCandidate() {
        candidate("current");
        var service = new RoutingPreflight(access, conversations, agent, gate, "live", true, 100);
        assertThat(service.resolve("owner", "continue", false, "auto", null, null, null, "current", 60)
                .needsClarification()).isTrue();
        verify(agent, never()).resolveRouting(any());
        when(gate.accepted()).thenReturn(true);
        when(agent.resolveRouting(any())).thenReturn(Map.of("schema_version", "intent-routing.v1", "status", "ready",
                "target", "existing", "target_session_id", "invented", "mode", "rag"));
        assertThat(service.resolve("owner", "continue", false, "auto", null, null, null, "current", 60)
                .needsClarification()).isTrue();
    }

    @Test void newWithExistingTargetIsRejectedBeforeAnyModelCall() {
        when(access.matchingConversation(eq("owner"), eq("current"), isNull())).thenReturn("current");
        var service = new RoutingPreflight(access, conversations, agent, gate, "shadow", false, 0);
        assertThatThrownBy(() -> service.resolve("owner", "new", false, "new", "current", null, null, null, 60))
                .isInstanceOf(ResponseStatusException.class)
                .satisfies(error -> assertThat(((ResponseStatusException) error).getStatusCode().value()).isEqualTo(400));
        verifyNoInteractions(agent);
    }

    @Test void technicalFailureClarifiesUnknownTargetButFallsBackForLockedTarget() {
        candidate("current"); when(gate.accepted()).thenReturn(true);
        when(agent.resolveRouting(any())).thenThrow(new IllegalStateException("timeout"));
        var service = new RoutingPreflight(access, conversations, agent, gate, "live", true, 100);
        assertThat(service.resolve("owner", "continue", false, "auto", null, null, null, "current", 60)
                .needsClarification()).isTrue();
        when(access.matchingConversation(eq("owner"), eq("current"), isNull())).thenReturn("current");
        var resolved = service.resolve("owner", "continue", false, "explicit", "current", null, null, null, 60);
        assertThat(resolved.decision()).containsEntry("mode", "rag").containsEntry("source", "fallback");
    }
}
