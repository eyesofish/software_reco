package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationMessageEntity;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessageEntity, String> {
    Page<ConversationMessageEntity> findByConversation_IdOrderByCreatedAtDesc(String conversationId, Pageable pageable);
}
