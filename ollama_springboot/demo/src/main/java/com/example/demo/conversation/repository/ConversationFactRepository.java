package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationFactEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ConversationFactRepository extends JpaRepository<ConversationFactEntity, String> {
    Optional<ConversationFactEntity> findByConversation_IdAndFactKey(String conversationId, String factKey);

    List<ConversationFactEntity> findByConversation_IdOrderByUpdatedAtDesc(String conversationId);
}
