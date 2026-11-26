package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.RecommendTaskEntity;
import com.example.demo.conversation.entity.RecommendTaskStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface RecommendTaskRepository extends JpaRepository<RecommendTaskEntity, String> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select t from RecommendTaskEntity t where t.id = :id")
    Optional<RecommendTaskEntity> lockById(@Param("id") String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select t from RecommendTaskEntity t where t.id = :id and t.conversation.id in " +
            "(select c.id from ConversationEntity c where c.ownerUserId = :owner and c.deleted = false)")
    Optional<RecommendTaskEntity> lockOwnedById(@Param("owner") String owner, @Param("id") String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    List<RecommendTaskEntity> findByConversation_IdAndStatusIn(String conversationId, Collection<RecommendTaskStatus> statuses);

    List<RecommendTaskEntity> findByConversation_IdOrderByCreatedAtDesc(String conversationId);

    Optional<RecommendTaskEntity> findTopByConversation_IdOrderByCreatedAtDesc(String conversationId);

    Optional<RecommendTaskEntity> findTopByFastapiSessionIdOrderByCreatedAtDesc(String fastapiSessionId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select t from RecommendTaskEntity t where t.status in :statuses and (t.lastHeartbeatAt is null or t.lastHeartbeatAt < :cutoff or t.expireAt < :now)")
    List<RecommendTaskEntity> lockStalledCandidates(@Param("statuses") Collection<RecommendTaskStatus> statuses,
            @Param("cutoff") java.time.Instant cutoff, @Param("now") java.time.Instant now);
}
