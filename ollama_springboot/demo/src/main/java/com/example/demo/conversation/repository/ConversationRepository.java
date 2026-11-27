package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import java.util.Optional;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ConversationRepository
        extends JpaRepository<ConversationEntity, String>, JpaSpecificationExecutor<ConversationEntity> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from ConversationEntity c where c.id = :id")
    Optional<ConversationEntity> lockById(@Param("id") String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from ConversationEntity c where c.id = (select t.conversation.id from RecommendTaskEntity t where t.id = :taskId)")
    Optional<ConversationEntity> lockForTask(@Param("taskId") String taskId);

    Optional<ConversationEntity> findByIdAndDeletedFalse(String id);
}
