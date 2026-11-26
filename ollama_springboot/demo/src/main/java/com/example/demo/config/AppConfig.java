package com.example.demo.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import io.netty.channel.ChannelOption;
import reactor.netty.http.client.HttpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;


@Configuration
public class AppConfig {
    private static final Logger logger = LoggerFactory.getLogger(AppConfig.class);

    @Bean("agentRestTemplate")
    public RestTemplate restTemplate(RestTemplateBuilder builder,
            @Value("${app.fastapi.api-key}") String apiKey) {
        requireAgentKey(apiKey);
        return builder
                .defaultHeader("X-API-Key", apiKey)
                .requestFactory(SimpleClientHttpRequestFactory::new)
                .connectTimeout(java.time.Duration.ofSeconds(3))
                .readTimeout(java.time.Duration.ofSeconds(310))
                .additionalInterceptors((request, body, execution) -> {
                    logger.info(
                            "Agent request method={}, uri={}",
                            request.getMethod(),
                            request.getURI());
                    return execution.execute(request, body);
                })
                .build();
    }

    @Bean("agentWebClient")
    public WebClient webClient(WebClient.Builder builder,
            @Value("${app.fastapi.api-key}") String apiKey) {
        requireAgentKey(apiKey);
        return builder
                .defaultHeader("X-API-Key", apiKey)
                .clientConnector(new ReactorClientHttpConnector(HttpClient.create()
                        .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 3000)))
                .codecs(configurer -> configurer.defaultCodecs().maxInMemorySize(16 * 1024 * 1024))
                .filter((request, next) -> {
                    logger.info(
                            "Agent stream request method={}, uri={}",
                            request.method(),
                            request.url());
                    return next.exchange(request);
                })
                .build();
    }

    private static void requireAgentKey(String key) {
        if (key == null || key.isBlank()) throw new IllegalStateException("AGENT_API_KEY is required");
    }

}
