package com.example.demo.auth;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class DemoUsers implements ApplicationRunner {
    private final UserRepository users;
    private final PasswordEncoder passwords;
    private final String alicePassword;
    private final String bobPassword;
    public DemoUsers(UserRepository users, PasswordEncoder passwords,
            @Value("${app.auth.alice-password}") String alice,
            @Value("${app.auth.bob-password}") String bob) {
        this.users = users; this.passwords = passwords; this.alicePassword = alice; this.bobPassword = bob;
    }
    @Override @Transactional
    public void run(ApplicationArguments args) { seed("alice", alicePassword); seed("bob", bobPassword); }
    private void seed(String username, String password) {
        int bytes = password.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
        if (bytes < 8 || bytes > 72)
            throw new IllegalStateException("Demo passwords must contain 8-72 bytes");
        if (users.findByUsername(username).isPresent()) return;
        AppUser user = new AppUser();
        user.setUsername(username); user.setPasswordHash(passwords.encode(password));
        users.save(user);
    }
}
