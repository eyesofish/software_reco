package com.example.demo.conversation.repository;

import com.example.demo.conversation.entity.ConversationEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

import java.util.Optional;

public interface ConversationRepository
        extends JpaRepository<ConversationEntity, String>, JpaSpecificationExecutor<ConversationEntity> {
    Optional<ConversationEntity> findByIdAndDeletedFalse(String id);
}
