package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.RecommendTaskEntity;
import com.example.demo.conversation.entity.RecommendTaskStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface RecommendTaskRepository extends JpaRepository<RecommendTaskEntity, String> {
    List<RecommendTaskEntity> findByConversation_IdOrderByCreatedAtDesc(String conversationId);

    Optional<RecommendTaskEntity> findTopByConversation_IdOrderByCreatedAtDesc(String conversationId);

    Optional<RecommendTaskEntity> findTopByFastapiSessionIdOrderByCreatedAtDesc(String fastapiSessionId);

    List<RecommendTaskEntity> findByStatusIn(Collection<RecommendTaskStatus> statuses);
}