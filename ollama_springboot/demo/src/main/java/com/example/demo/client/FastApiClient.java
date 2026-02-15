package com.example.demo.client;

import com.example.demo.dto.RecommendRequest;
import com.example.demo.dto.RecommendResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class FastApiClient {
    private static final Logger logger = LoggerFactory.getLogger(FastApiClient.class);
    private final RestTemplate restTemplate;
    private final String recommendUrl;

    public FastApiClient(
            RestTemplate restTemplate,
            @Value("${app.fastapi.base-url}") String baseUrl,
            @Value("${app.fastapi.recommend-path}") String recommendPath) {
        this.restTemplate = restTemplate;
        this.recommendUrl = baseUrl + recommendPath;
    }

    public RecommendResponse recommend(RecommendRequest request) {
        // 1. Header
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);

        // 2. Body（你现在这个 payload 是 OK 的）
        Map<String, Object> payload = new HashMap<>();
        payload.put("query", request.getQuery());
        payload.put("timeout", request.getTimeout());
        payload.put("max_iterations", request.getMaxIterations());

        // 3. HttpEntity = headers + body
        HttpEntity<Map<String, Object>> entity = new HttpEntity<>(payload, headers);

        logger.info(
                "FastAPI request url={}, payload={}, entity={}",
                recommendUrl, payload, entity);

        // 4. exchange：发请求 + 拿响应
        ResponseEntity<RecommendResponse> response = restTemplate.exchange(
                recommendUrl,
                HttpMethod.POST,
                entity,
                RecommendResponse.class);

        // 5. 直接返回 body
        return response.getBody();
    }

}
