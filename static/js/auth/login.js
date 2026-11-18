
/* =========================================================
   MEDIVERA — Login Page JavaScript
   ========================================================= */

document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    // 1. Password visibility toggle
    const passwordInput = document.querySelector(
        '.login-form input[name="password"]'
    );

    const passwordToggle = document.querySelector(
        ".password-toggle"
    );

    if (passwordInput && passwordToggle) {
        passwordToggle.setAttribute("type", "button");
        passwordToggle.setAttribute("aria-pressed", "false");

        passwordToggle.addEventListener("click", function () {
            const showPassword = passwordInput.type === "password";

            passwordInput.type = showPassword ? "text" : "password";

            passwordToggle.setAttribute(
                "aria-pressed",
                String(showPassword)
            );

            passwordToggle.setAttribute(
                "aria-label",
                showPassword ? "Hide password" : "Show password"
            );

            passwordInput.focus();
        });
    }

    // 2. Login button loading animation
    const loginForm = document.querySelector(".login-form");
    const loginButton = document.querySelector(".login-button");
    const buttonLabel = document.querySelector(".button-label");
    const buttonSpinner = document.querySelector(".button-spinner");
    const buttonArrow = document.querySelector(".button-arrow");

    if (loginForm && loginButton) {
        let isSubmitting = false;

        loginForm.addEventListener("submit", function (event) {
            if (isSubmitting) {
                event.preventDefault();
                return;
            }

            // Respect native HTML form validation.
            if (!loginForm.checkValidity()) {
                return;
            }

            isSubmitting = true;

            loginButton.disabled = true;
            loginButton.setAttribute("aria-busy", "true");

            if (buttonLabel) {
                buttonLabel.textContent = "Signing you in...";
            }

           loginButton.classList.add("is-loading");
        });
    }
});
