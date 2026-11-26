package com.example.demo.auth;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.time.Instant;
import java.util.List;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final UserRepository users;
    private final PasswordEncoder passwords;
    private final JwtEncoder encoder;
    private final String dummyHash;
    public AuthController(UserRepository users, PasswordEncoder passwords, JwtEncoder encoder) {
        this.users = users; this.passwords = passwords; this.encoder = encoder;
        this.dummyHash = passwords.encode("unused-auth-timing-value");
    }
    public record LoginRequest(@NotBlank @Size(max = 80) String username,
                               @NotBlank @Size(max = 72) String password) {}
    public record UserView(String id, String username) {}
    public record LoginResponse(String accessToken, String tokenType, int expiresIn, UserView user) {}

    @PostMapping("/login")
    public LoginResponse login(@Valid @RequestBody LoginRequest request) {
        if (request.password().getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 72)
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid credentials");
        AppUser user = users.findByUsername(request.username()).orElse(null);
        boolean matches = passwords.matches(request.password(), user == null ? dummyHash : user.getPasswordHash());
        if (!matches || user == null || !user.isEnabled())
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid credentials");
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder().issuer(AuthConfig.ISSUER)
                .audience(List.of(AuthConfig.AUDIENCE)).subject(user.getId())
                .issuedAt(now).expiresAt(now.plusSeconds(1800)).build();
        String token = encoder.encode(JwtEncoderParameters.from(
                JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
        return new LoginResponse(token, "Bearer", 1800, new UserView(user.getId(), user.getUsername()));
    }

    @GetMapping("/me")
    public UserView me(@AuthenticationPrincipal Jwt jwt) {
        AppUser user = users.findById(jwt.getSubject()).orElseThrow(
                () -> new ResponseStatusException(HttpStatus.UNAUTHORIZED));
        return new UserView(user.getId(), user.getUsername());
    }
}
