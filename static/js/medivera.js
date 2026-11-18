
document.addEventListener("DOMContentLoaded", () => {
    "use strict";

    /* =====================================================
       PASSWORD VISIBILITY
    ===================================================== */

    const passwordInput = document.getElementById("password");

    const passwordToggle =
        document.getElementById("passwordToggle") ||
        document.getElementById("togglePassword");

    if (passwordToggle && passwordInput) {
        passwordToggle.addEventListener("click", () => {
            const reveal = passwordInput.type === "password";

            passwordInput.type = reveal ? "text" : "password";
            passwordToggle.textContent = reveal ? "Hide" : "Show";

            passwordToggle.setAttribute(
                "aria-label",
                reveal ? "Hide password" : "Show password"
            );

            passwordToggle.setAttribute(
                "aria-pressed",
                String(reveal)
            );
        });
    }

    /* =====================================================
       RESPONSIVE PATIENT SIDEBAR
       One handler only; no duplicate click listeners.
    ===================================================== */

    const menuToggle = document.getElementById("menuToggle");
    const sidebar = document.getElementById("sidebar");

    if (!menuToggle || !sidebar) {
        return;
    }

    const mobileBreakpoint = 900;

    let overlay =
        document.getElementById("sidebarBackdrop");

    if (!overlay) {
        overlay = document.createElement("button");
        overlay.type = "button";
        overlay.className = "sidebar-overlay";
        overlay.setAttribute(
            "aria-label",
            "Close navigation menu"
        );
        overlay.setAttribute("aria-hidden", "true");
        overlay.tabIndex = -1;
        document.body.appendChild(overlay);
    }

    menuToggle.setAttribute("aria-controls", "sidebar");
    menuToggle.setAttribute("aria-expanded", "false");

    if (!menuToggle.hasAttribute("aria-label")) {
        menuToggle.setAttribute(
            "aria-label",
            "Open navigation menu"
        );
    }

    const isMobile = () =>
        window.innerWidth <= mobileBreakpoint;

    function openSidebar() {
        if (!isMobile()) return;

        sidebar.classList.add("is-open");
        document.body.classList.add("sidebar-open");

        overlay.classList.add("is-visible");
        overlay.setAttribute("aria-hidden", "false");

        menuToggle.setAttribute("aria-expanded", "true");
        menuToggle.setAttribute(
            "aria-label",
            "Close navigation menu"
        );

        const firstLink = sidebar.querySelector(
            "a.nav-link:not(.nav-placeholder)"
        );

        if (firstLink) {
            firstLink.focus({ preventScroll: true });
        }
    }

    function closeSidebar(restoreFocus = false) {
        sidebar.classList.remove("is-open");
        document.body.classList.remove("sidebar-open");

        overlay.classList.remove("is-visible");
        overlay.setAttribute("aria-hidden", "true");

        menuToggle.setAttribute("aria-expanded", "false");
        menuToggle.setAttribute(
            "aria-label",
            "Open navigation menu"
        );

        if (restoreFocus && isMobile()) {
            menuToggle.focus({ preventScroll: true });
        }
    }

    menuToggle.addEventListener("click", () => {
        if (sidebar.classList.contains("is-open")) {
            closeSidebar(true);
        } else {
            openSidebar();
        }
    });

    overlay.addEventListener("click", () => {
        closeSidebar(true);
    });

    sidebar.addEventListener("click", (event) => {
        const link = event.target.closest("a.nav-link");

        if (
            link &&
            !link.classList.contains("nav-placeholder") &&
            isMobile()
        ) {
            closeSidebar(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (
            event.key === "Escape" &&
            sidebar.classList.contains("is-open")
        ) {
            closeSidebar(true);
        }
    });

    window.addEventListener("resize", () => {
        if (!isMobile()) {
            closeSidebar(false);
        }
    });

    document.addEventListener("touchmove", (event) => {
        if (
            document.body.classList.contains("sidebar-open") &&
            !sidebar.contains(event.target)
        ) {
            event.preventDefault();
        }
    }, { passive: false });
});
