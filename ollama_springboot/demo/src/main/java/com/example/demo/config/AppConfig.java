package com.example.demo.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.BufferingClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.nio.charset.StandardCharsets;

@Configuration
public class AppConfig {
    private static final Logger logger = LoggerFactory.getLogger(AppConfig.class);

    @Bean
    public RestTemplate restTemplate(RestTemplateBuilder builder) {
        return builder
                .requestFactory(() -> new BufferingClientHttpRequestFactory(
                        new SimpleClientHttpRequestFactory()))
                .additionalInterceptors((request, body, execution) -> {
                    String rawBody = new String(body, StandardCharsets.UTF_8);
                    String bodyText = rawBody.contains("\"data_url\"")
                            ? "<multimodal request body redacted; " + body.length + " bytes>"
                            : rawBody;
                    logger.info(
                            "RestTemplate request method={}, uri={}, headers={}, body={}",
                            request.getMethod(),
                            request.getURI(),
                            request.getHeaders(),
                            bodyText);
                    return execution.execute(request, body);
                })
                .build();
    }

    @Bean
    public WebClient webClient(WebClient.Builder builder) {
        return builder
                .codecs(configurer -> configurer.defaultCodecs().maxInMemorySize(16 * 1024 * 1024))
                .filter((request, next) -> {
                    logger.info(
                            "WebClient request method={}, uri={}, headers={}",
                            request.method(),
                            request.url(),
                            request.headers());
                    return next.exchange(request);
                })
                .build();
    }

    @Bean
    public WebMvcConfigurer corsConfigurer(
            @Value("${app.cors.allowed-origins}") String allowedOrigins) {
        return new WebMvcConfigurer() {
            @Override
            public void addCorsMappings(CorsRegistry registry) {
                registry.addMapping("/**")
                        .allowedOrigins(allowedOrigins.split(","))
                        .allowedMethods("*")
                        .allowedHeaders("*");
            }
        };
    }
}
