
/* ============================================================
   MEDICARE+ | SMART HEALTHCARE MANAGEMENT SYSTEM
   MAIN JAVASCRIPT
   ============================================================ */

"use strict";


/* ============================================================
   01. APPLICATION INITIALIZATION
   ============================================================ */

document.addEventListener("DOMContentLoaded", function () {

    Medicare.init();

});


/* ============================================================
   02. MAIN APPLICATION OBJECT
   ============================================================ */

const Medicare = {

    init: function () {

        this.initializeSidebar();
        this.initializeNavigation();
        this.initializeSearch();
        this.initializeNotifications();
        this.initializeProfileMenu();
        this.initializeKeyboardShortcuts();
        this.initializeForms();
        this.initializeTooltips();
        this.initializeClock();

        console.log("MediCare+ initialized successfully.");

    },


    /* ========================================================
       03. SIDEBAR MANAGEMENT
       ======================================================== */

    initializeSidebar: function () {

        const sidebar = document.getElementById("sidebar");
        const menuToggle = document.getElementById("menuToggle");
        const sidebarClose = document.getElementById("sidebarClose");
        const overlay = document.getElementById("sidebarOverlay");

        if (!sidebar) return;

        const openSidebar = () => {

            sidebar.classList.add("open");

            if (overlay) {
                overlay.classList.add("active");
            }

            document.body.style.overflow = "hidden";

        };


        const closeSidebar = () => {

            sidebar.classList.remove("open");

            if (overlay) {
                overlay.classList.remove("active");
            }

            document.body.style.overflow = "";

        };


        if (menuToggle) {
            menuToggle.addEventListener("click", openSidebar);
        }

        if (sidebarClose) {
            sidebarClose.addEventListener("click", closeSidebar);
        }

        if (overlay) {
            overlay.addEventListener("click", closeSidebar);
        }


        document.addEventListener("keydown", function (event) {

            if (event.key === "Escape") {
                closeSidebar();
            }

        });


        window.addEventListener("resize", function () {

            if (window.innerWidth > 700) {
                closeSidebar();
            }

        });

    },


    /* ========================================================
       04. NAVIGATION MANAGEMENT
       ======================================================== */

    initializeNavigation: function () {

        const links = document.querySelectorAll(".nav-link");

        links.forEach(function (link) {

            link.addEventListener("click", function (event) {

                const href = link.getAttribute("href");

                // Do not navigate through placeholder links.
                if (!href || href === "#") {
                    event.preventDefault();

                    Medicare.showToast(
                        "This module will be connected soon.",
                        "info"
                    );

                    return;
                }


                links.forEach(function (item) {

                    item.classList.remove("active");

                });

                link.classList.add("active");

            });

        });

    },


    /* ========================================================
       05. SEARCH MANAGEMENT
       ======================================================== */

    initializeSearch: function () {

        const searchInput = document.querySelector(
            ".header-search input"
        );

        if (!searchInput) return;


        searchInput.addEventListener("keydown", function (event) {

            if (event.key === "Enter") {

                const query = searchInput.value.trim();

                if (!query) {

                    Medicare.showToast(
                        "Please enter something to search.",
                        "warning"
                    );

                    return;

                }

                Medicare.performSearch(query);

            }

        });


        searchInput.addEventListener("input", function () {

            if (searchInput.value.length > 100) {

                searchInput.value =
                    searchInput.value.substring(0, 100);

            }

        });

    },


    performSearch: function (query) {

        console.log("Search requested:", query);

        Medicare.showToast(
            "Search interface is ready. Database search will be connected next.",
            "info"
        );

    },


    /* ========================================================
       06. NOTIFICATION MANAGEMENT
       ======================================================== */

    initializeNotifications: function () {

        const button = document.getElementById(
            "notificationButton"
        );

        if (!button) return;


        button.addEventListener("click", function () {

            Medicare.showToast(
                "No notification panel is connected yet.",
                "info"
            );

        });

    },


    /* ========================================================
       07. PROFILE MENU
       ======================================================== */

    initializeProfileMenu: function () {

        const button = document.querySelector(
            ".profile-menu-button"
        );

        if (!button) return;


        button.addEventListener("click", function () {

            Medicare.showToast(
                "Profile settings will be available soon.",
                "info"
            );

        });

    },


    /* ========================================================
       08. KEYBOARD SHORTCUTS
       ======================================================== */

    initializeKeyboardShortcuts: function () {

        document.addEventListener("keydown", function (event) {

            const isMac =
                navigator.platform.toUpperCase().includes("MAC");

            const modifier = isMac ? event.metaKey : event.ctrlKey;


            // Ctrl + K: Focus search.
            if (modifier && event.key.toLowerCase() === "k") {

                event.preventDefault();

                const searchInput = document.querySelector(
                    ".header-search input"
                );

                if (searchInput) {
                    searchInput.focus();
                }

            }


            // Escape: Close menus.
            if (event.key === "Escape") {

                const searchInput = document.querySelector(
                    ".header-search input"
                );

                if (
                    searchInput &&
                    document.activeElement === searchInput
                ) {
                    searchInput.blur();
                }

            }

        });

    },


    /* ========================================================
       09. FORM INITIALIZATION
       ======================================================== */

    initializeForms: function () {

        const forms = document.querySelectorAll(
            "form[data-validate='true']"
        );


        forms.forEach(function (form) {

            form.addEventListener("submit", function (event) {

                if (!form.checkValidity()) {

                    event.preventDefault();

                    form.reportValidity();

                    Medicare.showToast(
                        "Please complete the required fields.",
                        "warning"
                    );

                }

            });

        });

    },


    /* ========================================================
       10. TOOLTIP INITIALIZATION
       ======================================================== */

    initializeTooltips: function () {

        const elements = document.querySelectorAll(
            "[data-tooltip]"
        );


        elements.forEach(function (element) {

            element.setAttribute(
                "title",
                element.getAttribute("data-tooltip")
            );

        });

    },


    /* ========================================================
       11. CLOCK INITIALIZATION
       ======================================================== */

    initializeClock: function () {

        const clock = document.getElementById(
            "liveClock"
        );

        if (!clock) return;


        const updateClock = () => {

            const now = new Date();

            clock.textContent =
                now.toLocaleTimeString();

        };


        updateClock();

        window.setInterval(updateClock, 1000);

    },


    /* ========================================================
       12. TOAST NOTIFICATION SYSTEM
       ======================================================== */

    showToast: function (message, type = "success") {

        let container = document.getElementById(
            "toastContainer"
        );


        if (!container) {

            container = document.createElement("div");

            container.id = "toastContainer";

            container.style.position = "fixed";
            container.style.top = "20px";
            container.style.right = "20px";
            container.style.zIndex = "9999";

            container.style.display = "flex";
            container.style.flexDirection = "column";
            container.style.gap = "10px";

            document.body.appendChild(container);

        }


        const toast = document.createElement("div");

        toast.className = "medicare-toast";

        const colors = {

            success: "#245B43",
            danger: "#8F2635",
            warning: "#B57B28",
            info: "#557C8C"

        };


        toast.style.background = "#FFFFFF";
        toast.style.color = "#20221F";
        toast.style.borderLeft =
            "4px solid " + (colors[type] || colors.success);

        toast.style.padding = "13px 17px";
        toast.style.borderRadius = "9px";
        toast.style.boxShadow =
            "0 8px 25px rgba(0,0,0,0.12)";

        toast.style.fontSize = "12px";
        toast.style.minWidth = "240px";
        toast.style.maxWidth = "340px";

        toast.style.animation =
            "medicareToastIn 0.25s ease";

        toast.setAttribute("role", "status");

        toast.textContent = message;

        container.appendChild(toast);


        window.setTimeout(function () {

            toast.style.opacity = "0";
            toast.style.transform = "translateX(20px)";

            window.setTimeout(function () {

                toast.remove();

                if (container.children.length === 0) {
                    container.remove();
                }

            }, 300);

        }, 3500);

    },


    /* ========================================================
       13. CONFIRMATION DIALOG
       ======================================================== */

    confirmAction: function (
        message,
        callback
    ) {

        const result = window.confirm(message);

        if (result && typeof callback === "function") {

            callback();

        }

        return result;

    },


    /* ========================================================
       14. BUTTON LOADING STATE
       ======================================================== */

    setButtonLoading: function (
        button,
        loading = true
    ) {

        if (!button) return;


        if (loading) {

            button.dataset.originalText =
                button.innerHTML;

            button.disabled = true;

            button.innerHTML =
                '<span class="loading-spinner"></span> Please wait...';

        } else {

            button.disabled = false;

            button.innerHTML =
                button.dataset.originalText || "Submit";

        }

    },


    /* ========================================================
       15. INPUT VALIDATION HELPERS
       ======================================================== */

    validateEmail: function (email) {

        const pattern =
            /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        return pattern.test(email);

    },


    validatePhone: function (phone) {

        const pattern =
            /^\+?[0-9\s\-()]{7,20}$/;

        return pattern.test(phone);

    },


    validateRequired: function (value) {

        return (
            value !== null &&
            value !== undefined &&
            String(value).trim().length > 0
        );

    },


    /* ========================================================
       16. CURRENCY FORMATTING
       ======================================================== */

    formatCurrency: function (
        amount,
        currency = "INR"
    ) {

        const number = Number(amount);

        if (!Number.isFinite(number)) {
            return "₹0.00";
        }

        return new Intl.NumberFormat(
            "en-IN",
            {
                style: "currency",
                currency: currency,
                maximumFractionDigits: 2
            }
        ).format(number);

    },


    /* ========================================================
       17. DATE FORMATTING
       ======================================================== */

    formatDate: function (dateValue) {

        const date = new Date(dateValue);

        if (Number.isNaN(date.getTime())) {
            return "Invalid date";
        }

        return date.toLocaleDateString(
            "en-IN",
            {
                day: "2-digit",
                month: "short",
                year: "numeric"
            }
        );

    },


    /* ========================================================
       18. DEBOUNCE HELPER
       ======================================================== */

    debounce: function (callback, delay = 300) {

        let timer;

        return function (...args) {

            window.clearTimeout(timer);

            timer = window.setTimeout(function () {

                callback.apply(this, args);

            }, delay);

        };

    },


    /* ========================================================
       19. SAFE TEXT HELPER
       ======================================================== */

    escapeHTML: function (value) {

        const element = document.createElement("div");

        element.textContent = String(value ?? "");

        return element.innerHTML;

    },


    /* ========================================================
       20. PRINT HELPER
       ======================================================== */

    printPage: function () {

        window.print();

    },


    /* ========================================================
       21. TABLE EXPORT HELPER
       ======================================================== */

    exportTableToCSV: function (
        tableId,
        filename = "medicare-report.csv"
    ) {

        const table = document.getElementById(tableId);

        if (!table) {

            this.showToast(
                "Table not found.",
                "danger"
            );

            return;

        }


        const rows = Array.from(
            table.querySelectorAll("tr")
        );


        const csv = rows.map(function (row) {

            const cells = Array.from(
                row.querySelectorAll("th, td")
            );

            return cells.map(function (cell) {

                const value =
                    cell.innerText.replace(/"/g, '""');

                return '"' + value + '"';

            }).join(",");

        }).join("\n");


        const blob = new Blob(
            ["\uFEFF" + csv],
            {
                type: "text/csv;charset=utf-8;"
            }
        );


        const url = URL.createObjectURL(blob);

        const link = document.createElement("a");

        link.href = url;
        link.download = filename;

        document.body.appendChild(link);

        link.click();

        link.remove();

        URL.revokeObjectURL(url);

        this.showToast(
            "CSV export generated successfully.",
            "success"
        );

    },


    /* ========================================================
       22. SCROLL TO TOP
       ======================================================== */

    scrollToTop: function () {

        window.scrollTo({

            top: 0,
            behavior: "smooth"

        });

    },


    /* ========================================================
       23. SIDEBAR ACTIVE LINK
       ======================================================== */

    setActiveNavigation: function (url) {

        const links = document.querySelectorAll(
            ".nav-link"
        );


        links.forEach(function (link) {

            link.classList.remove("active");

            if (link.href === url) {

                link.classList.add("active");

            }

        });

    },


    /* ========================================================
       24. LOCAL FORM RESET
       ======================================================== */

    resetForm: function (formId) {

        const form = document.getElementById(formId);

        if (!form) return;

        form.reset();

        this.showToast(
            "Form has been reset.",
            "info"
        );

    },


    /* ========================================================
       25. APPLICATION INFORMATION
       ======================================================== */

    getAppInformation: function () {

        return {

            name: "MediCare+",

            version: "1.0.0",

            application:
                "Smart Healthcare Management System",

            theme: "Beige Red Green Black White",

            framework: "Flask",

            database: "MySQL"

        };

    }

};


/* ============================================================
   26. GLOBAL ACCESS
   ============================================================ */

window.Medicare = Medicare;


/* ============================================================
   27. TOAST ANIMATION
   ============================================================ */

const toastStyle = document.createElement("style");

toastStyle.textContent = `

@keyframes medicareToastIn {

    from {

        opacity: 0;

        transform: translateX(20px);

    }

    to {

        opacity: 1;

        transform: translateX(0);

    }

}

.medicare-toast {

    transition:
        opacity 0.3s ease,
        transform 0.3s ease;

}

`;

document.head.appendChild(toastStyle);


/* ============================================================
   28. END OF MEDICARE+ JAVASCRIPT
   ============================================================ */
