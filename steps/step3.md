File: D:\Github\software_reco\ollama_springboot\demo\pom.xml
```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter</artifactId>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-validation</artifactId>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>

    <dependency>
        <groupId>org.flywaydb</groupId>
        <artifactId>flyway-core</artifactId>
    </dependency>

    <dependency>
        <groupId>org.postgresql</groupId>
        <artifactId>postgresql</artifactId>
        <scope>runtime</scope>
    </dependency>

    <dependency>
        <groupId>com.h2database</groupId>
        <artifactId>h2</artifactId>
        <scope>runtime</scope>
    </dependency>

    <dependency>
        <groupId>org.projectlombok</groupId>
        <artifactId>lombok</artifactId>
        <optional>true</optional>
    </dependency>

    <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
        <version>2.20.1</version>
        <scope>compile</scope>
    </dependency>

    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-test</artifactId>
        <scope>test</scope>
    </dependency>
</dependencies>
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\resources\application.yml
```yaml
server:
  port: 8080

spring:
  jackson:
    property-naming-strategy: SNAKE_CASE
  datasource:
    url: jdbc:h2:mem:software_reco;MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE
    username: sa
    password:
    driver-class-name: org.h2.Driver
  jpa:
    hibernate:
      ddl-auto: validate
    open-in-view: false
    properties:
      hibernate:
        dialect: org.hibernate.dialect.H2Dialect
  flyway:
    enabled: true
    locations: classpath:db/migration
  h2:
    console:
      enabled: true
      path: /h2-console

app:
  fastapi:
    base-url: http://127.0.0.1:8000
    recommend-path: /api/v1/recommend
  cors:
    allowed-origins: http://localhost:3000,http://127.0.0.1:3000

---
spring:
  config:
    activate:
      on-profile: postgres
  datasource:
    url: ${DB_URL:jdbc:postgresql://localhost:5432/software_reco}
    username: ${DB_USER:postgres}
    password: ${DB_PASSWORD:postgres}
    driver-class-name: org.postgresql.Driver
  jpa:
    properties:
      hibernate:
        dialect: org.hibernate.dialect.PostgreSQLDialect
  h2:
    console:
      enabled: false
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\resources\db\migration\V1__create_conversation_tables.sql
```sql
CREATE TABLE conversations (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    preview VARCHAR(500),
    model_name VARCHAR(128),
    message_count INTEGER NOT NULL DEFAULT 0,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    last_message_role VARCHAR(16),
    last_message_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX idx_conversation_updated_at ON conversations(updated_at DESC);
CREATE INDEX idx_conversation_deleted_archived ON conversations(deleted, archived);

CREATE TABLE conversation_messages (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT fk_conversation_messages_conversation
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX idx_message_conversation_created_at
  ON conversation_messages(conversation_id, created_at ASC);
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\entity\ConversationEntity.java
```java
// No change required
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\java\com\example\demo\conversation\entity\ConversationMessageEntity.java
```java
// No change required
```

File: D:\Github\software_reco\ollama_springboot\demo\src\main\resources\db\migration\V2__h2_compat.sql
```sql
-- No additional migration script is required.
```

File: D:\Github\software_reco\ollama_springboot\demo\Run commands
```bash
cd D:\Github\software_reco\ollama_springboot\demo
mvn test
mvn spring-boot:run -Dspring-boot.run.arguments="--server.port=8081"
```

File: D:\Github\software_reco\ollama_springboot\demo\Optional PostgreSQL run
```bash
cd D:\Github\software_reco\ollama_springboot\demo
mvn spring-boot:run -Dspring-boot.run.profiles=postgres
```