/* ==========================================================
   MEDIVERA MOTION ENGINE
   Shared 3D interactions for all MEDIVERA dashboards
========================================================== */

(() => {
    "use strict";

    const start = () => {
        const reducedMotion = window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        ).matches;

        const finePointer = window.matchMedia(
            "(hover: hover) and (pointer: fine)"
        ).matches;

        const cardSelectors = [
            ".pd-stat-card",
            ".pd-panel",
            ".pd-service-card:not(.pd-service-disabled)",
            ".pd-quick-link",
            ".admin-stat-card",
            ".admin-management-card",
            ".admin-panel"
        ].join(",");

        const revealSelectors = [
            ".pd-hero",
            ".pd-section-heading",
            ".pd-stat-card",
            ".pd-panel",
            ".pd-service-card",
            ".admin-welcome",
            ".admin-stat-card",
            ".admin-panel",
            ".admin-management-card"
        ].join(",");

        const cards = Array.from(
            document.querySelectorAll(cardSelectors)
        );

        const revealItems = Array.from(
            document.querySelectorAll(revealSelectors)
        );

        if (!cards.length && !revealItems.length) {
            return;
        }

        /* Do not hide content if the browser lacks observers. */
        const canObserve = "IntersectionObserver" in window;

        if (!reducedMotion && canObserve) {
            document.documentElement.classList.add(
                "mv-motion-ready"
            );
        }

        /* Add 3D behaviour to eligible cards. */
        cards.forEach((card) => {
            if (card.matches(".pd-service-disabled")) {
                return;
            }

            card.classList.add("mv-3d-card");

            if (finePointer && !reducedMotion) {
                card.dataset.mvTilt = "true";
            }
        });

        /* Assign reveal classes and gentle stagger. */
        if (!reducedMotion && canObserve) {
            revealItems.forEach((item, index) => {
                item.classList.add("mv-reveal");

                const delay = (index % 4) + 1;
                item.dataset.mvDelay = String(delay);
            });

            const observer = new IntersectionObserver(
                (entries, currentObserver) => {
                    entries.forEach((entry) => {
                        if (!entry.isIntersecting) {
                            return;
                        }

                        entry.target.classList.add("mv-visible");
                        currentObserver.unobserve(entry.target);
                    });
                },
                {
                    threshold: 0.08,
                    rootMargin: "0px 0px -35px 0px"
                }
            );

            revealItems.forEach((item) => observer.observe(item));
        } else {
            revealItems.forEach((item) => {
                item.classList.add("mv-visible");
            });
        }

        /* Pointer-responsive 3D tilt. */
        if (finePointer && !reducedMotion) {
            cards.forEach((card) => {
                if (card.dataset.mvTilt !== "true") {
                    return;
                }

                let bounds = null;

                card.addEventListener("pointerenter", () => {
                    bounds = card.getBoundingClientRect();
                });

                card.addEventListener("pointermove", (event) => {
                    bounds = card.getBoundingClientRect();

                    if (!bounds.width || !bounds.height) {
                        return;
                    }

                    const x = (
                        (event.clientX - bounds.left) / bounds.width
                    );

                    const y = (
                        (event.clientY - bounds.top) / bounds.height
                    );

                    const rotateY = (x - 0.5) * 5;
                    const rotateX = (0.5 - y) * 5;

                    card.style.transform =
                        `perspective(900px) ` +
                        `rotateX(${rotateX}deg) ` +
                        `rotateY(${rotateY}deg) ` +
                        `translateY(-3px)`;
                });

                card.addEventListener("pointerleave", () => {
                    card.style.transform = "";
                    bounds = null;
                });
            });
        }

        /* Add a class for future page-level transitions. */
        document.documentElement.classList.add(
            "mv-motion-initialized"
        );

        console.info(
            "MEDIVERA Motion Engine initialized:",
            cards.length,
            "interactive surfaces"
        );
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, {
            once: true
        });
    } else {
        start();
    }
})();
