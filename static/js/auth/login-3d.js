import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js";

(() => {
    "use strict";

    const host = document.querySelector(".hero-image-wrap");

    if (!host || !window.WebGLRenderingContext) {
        return;
    }

    const canvas = document.createElement("canvas");
    canvas.className = "medivera-3d-canvas";
    canvas.setAttribute("aria-hidden", "true");
    host.prepend(canvas);

    let renderer;

    try {
        renderer = new THREE.WebGLRenderer({
            canvas,
            antialias: true,
            alpha: true,
            powerPreference: "high-performance"
        });
    } catch (error) {
        console.warn("MEDIVERA 3D unavailable:", error);
        canvas.remove();
        return;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.8));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.25;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10141b);
    scene.fog = new THREE.FogExp2(0x10141b, 0.075);

    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0, 10);

    scene.add(new THREE.AmbientLight(0xffffff, 1.7));

    const keyLight = new THREE.PointLight(0xff7130, 95, 18);
    keyLight.position.set(-3, 4, 5);
    scene.add(keyLight);

    const fillLight = new THREE.PointLight(0x4b9dff, 55, 18);
    fillLight.position.set(4, -2, 3);
    scene.add(fillLight);

    const rimLight = new THREE.PointLight(0xffa05a, 65, 15);
    rimLight.position.set(0, 2, -4);
    scene.add(rimLight);

    // Background orbital rings
    const ringMaterial = new THREE.MeshBasicMaterial({
        color: 0xff853d,
        transparent: true,
        opacity: 0.27
    });

    const rings = [];

    [2.15, 2.55, 2.95].forEach((radius, index) => {
        const ring = new THREE.Mesh(
            new THREE.TorusGeometry(radius, 0.009, 8, 120),
            ringMaterial
        );

        ring.rotation.x = Math.PI * (0.28 + index * 0.16);
        ring.rotation.y = index * 0.6;
        scene.add(ring);
        rings.push(ring);
    });

    // Heart: extruded, rounded 3D heart silhouette
    const heartShape = new THREE.Shape();

    heartShape.moveTo(0, -0.95);
    heartShape.bezierCurveTo(-0.35, -0.65, -1.35, 0.05, -1.18, 0.83);
    heartShape.bezierCurveTo(-1.04, 1.45, -0.25, 1.48, 0, 0.91);
    heartShape.bezierCurveTo(0.30, 1.50, 1.08, 1.43, 1.22, 0.80);
    heartShape.bezierCurveTo(1.40, 0.05, 0.36, -0.68, 0, -0.95);

    const heartGeometry = new THREE.ExtrudeGeometry(heartShape, {
        depth: 0.48,
        bevelEnabled: true,
        bevelSegments: 5,
        steps: 1,
        bevelSize: 0.12,
        bevelThickness: 0.12,
        curveSegments: 28
    });

    heartGeometry.center();

    const heartMaterial = new THREE.MeshPhysicalMaterial({
        color: 0xa91d32,
        roughness: 0.3,
        metalness: 0.05,
        clearcoat: 0.85,
        clearcoatRoughness: 0.18
    });

    const heart = new THREE.Mesh(heartGeometry, heartMaterial);
    heart.scale.set(0.9, 0.9, 0.9);
    scene.add(heart);

    // Blood-vessel-inspired upper structures
    const vesselMaterial = new THREE.MeshStandardMaterial({
        color: 0xd64b4d,
        roughness: 0.34,
        metalness: 0.08
    });

    function makeVessel(points, radius) {
        const curve = new THREE.CatmullRomCurve3(
            points.map(p => new THREE.Vector3(...p))
        );

        const mesh = new THREE.Mesh(
            new THREE.TubeGeometry(curve, 32, radius, 10, false),
            vesselMaterial
        );

        heart.add(mesh);
    }

    makeVessel([
        [-0.42, 0.85, 0.08],
        [-0.45, 1.25, 0.12],
        [-0.55, 1.47, 0.12]
    ], 0.13);

    makeVessel([
        [0.34, 0.85, 0.12],
        [0.45, 1.24, 0.12],
        [0.58, 1.43, 0.12]
    ], 0.12);

    // Materials for medical equipment
    const orange = new THREE.MeshStandardMaterial({
        color: 0xf47721,
        metalness: 0.55,
        roughness: 0.27
    });

    const silver = new THREE.MeshStandardMaterial({
        color: 0xbac5d2,
        metalness: 0.82,
        roughness: 0.22
    });

    const dark = new THREE.MeshStandardMaterial({
        color: 0x222b36,
        metalness: 0.55,
        roughness: 0.3
    });

    const glass = new THREE.MeshPhysicalMaterial({
        color: 0x7bc9ff,
        transparent: true,
        opacity: 0.38,
        roughness: 0.1,
        metalness: 0.1
    });

    // Orbital groups
    const orbiters = [];

    function addOrbiter(radius, speed, tilt, distance, object) {
        const pivot = new THREE.Group();
        pivot.rotation.z = tilt;
        pivot.position.z = distance;
        pivot.add(object);

        object.position.set(radius, 0, 0);

        scene.add(pivot);
        orbiters.push({ pivot, speed, object, phase: 0 });

        return pivot;
    }

    // ECG monitor
    const monitor = new THREE.Group();

    const monitorBody = new THREE.Mesh(
        new THREE.BoxGeometry(0.72, 0.49, 0.13),
        dark
    );
    monitor.add(monitorBody);

    const monitorScreen = new THREE.Mesh(
        new THREE.PlaneGeometry(0.58, 0.34),
        new THREE.MeshBasicMaterial({ color: 0x071b20 })
    );
    monitorScreen.position.z = 0.075;
    monitor.add(monitorScreen);

    const ecgPoints = [
        [-0.25, 0, 0.09],
        [-0.16, 0, 0.09],
        [-0.10, 0.09, 0.09],
        [-0.04, -0.12, 0.09],
        [0.02, 0.15, 0.09],
        [0.08, 0, 0.09],
        [0.25, 0, 0.09]
    ].map(p => new THREE.Vector3(...p));

    const ecgCurve = new THREE.CatmullRomCurve3(ecgPoints);

    monitor.add(new THREE.Mesh(
        new THREE.TubeGeometry(ecgCurve, 50, 0.012, 6, false),
        new THREE.MeshBasicMaterial({ color: 0x42ffb0 })
    ));

    addOrbiter(3.15, 0.36, 0.35, 0.15, monitor);

    // Syringe
    const syringe = new THREE.Group();

    const syringeBody = new THREE.Mesh(
        new THREE.CylinderGeometry(0.105, 0.105, 0.76, 20),
        glass
    );
    syringe.add(syringeBody);

    const syringePlunger = new THREE.Mesh(
        new THREE.CylinderGeometry(0.14, 0.14, 0.12, 20),
        orange
    );
    syringePlunger.position.y = 0.43;
    syringe.add(syringePlunger);

    const needle = new THREE.Mesh(
        new THREE.CylinderGeometry(0.012, 0.002, 0.38, 8),
        silver
    );
    needle.position.y = -0.55;
    syringe.add(needle);

    syringe.rotation.z = -0.7;
    addOrbiter(3.05, -0.27, -0.65, 0.35, syringe);

    // Microscope
    const microscope = new THREE.Group();

    const microscopeBase = new THREE.Mesh(
        new THREE.BoxGeometry(0.65, 0.12, 0.42),
        silver
    );
    microscopeBase.position.y = -0.32;
    microscope.add(microscopeBase);

    const microscopeStand = new THREE.Mesh(
        new THREE.CylinderGeometry(0.09, 0.12, 0.68, 16),
        dark
    );
    microscopeStand.position.set(0, 0.02, 0);
    microscope.add(microscopeStand);

    const microscopeHead = new THREE.Mesh(
        new THREE.CylinderGeometry(0.13, 0.16, 0.48, 16),
        orange
    );
    microscopeHead.rotation.z = Math.PI / 2.5;
    microscopeHead.position.set(0.15, 0.4, 0);
    microscope.add(microscopeHead);

    const eyepiece = new THREE.Mesh(
        new THREE.CylinderGeometry(0.08, 0.08, 0.26, 16),
        silver
    );
    eyepiece.position.set(0.36, 0.58, 0);
    microscope.add(eyepiece);

    addOrbiter(3.5, 0.19, 0.75, -0.25, microscope);

    // Stethoscope-inspired instrument
    const stethoscope = new THREE.Group();

    const stethoscopeCurve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(-0.25, 0.32, 0),
        new THREE.Vector3(-0.43, 0.05, 0),
        new THREE.Vector3(-0.25, -0.32, 0),
        new THREE.Vector3(0.10, -0.30, 0),
        new THREE.Vector3(0.30, -0.04, 0),
        new THREE.Vector3(0.22, 0.25, 0)
    ]);

    stethoscope.add(new THREE.Mesh(
        new THREE.TubeGeometry(stethoscopeCurve, 64, 0.045, 10, false),
        orange
    ));

    const chestpiece = new THREE.Mesh(
        new THREE.CylinderGeometry(0.17, 0.17, 0.10, 24),
        silver
    );
    chestpiece.position.set(0.22, 0.29, 0);
    stethoscope.add(chestpiece);

    addOrbiter(2.85, 0.31, -0.25, 0.55, stethoscope);

    // Scanner device
    const scanner = new THREE.Group();

    const scannerBody = new THREE.Mesh(
        new THREE.TorusGeometry(0.35, 0.075, 12, 48),
        silver
    );
    scanner.add(scannerBody);

    const scannerGlow = new THREE.Mesh(
        new THREE.TorusGeometry(0.24, 0.018, 8, 48),
        orange
    );
    scannerGlow.position.z = 0.04;
    scanner.add(scannerGlow);

    addOrbiter(3.45, -0.22, 0.5, -0.6, scanner);

    // Floating medical particles
    const particleCount = 95;
    const particlePositions = new Float32Array(particleCount * 3);

    for (let i = 0; i < particleCount; i++) {
        particlePositions[i * 3] = (Math.random() - 0.5) * 9;
        particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 6;
        particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 3;
    }

    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute(
        "position",
        new THREE.BufferAttribute(particlePositions, 3)
    );

    const particles = new THREE.Points(
        particleGeometry,
        new THREE.PointsMaterial({
            color: 0xffa36a,
            size: 0.035,
            transparent: true,
            opacity: 0.72
        })
    );

    scene.add(particles);

    const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
    ).matches;

    let width = 0;
    let height = 0;
    let frameId = 0;
    let pointerX = 0;
    let pointerY = 0;
    let currentX = 0;
    let currentY = 0;
    const clock = new THREE.Clock();

    function resize() {
        width = host.clientWidth;
        height = host.clientHeight;

        if (!width || !height) return;

        camera.aspect = width / height;
        camera.position.z = width < 450 ? 11.5 : 10;
        camera.updateProjectionMatrix();

        renderer.setSize(width, height, false);
    }

    function onPointerMove(event) {
        const rect = host.getBoundingClientRect();

        pointerX = ((event.clientX - rect.left) / rect.width - 0.5) * 0.7;
        pointerY = ((event.clientY - rect.top) / rect.height - 0.5) * 0.5;
    }

    host.addEventListener("pointermove", onPointerMove, { passive: true });

    function animate() {
        frameId = requestAnimationFrame(animate);

        const elapsed = clock.getElapsedTime();

        if (!reducedMotion) {
            const beat = Math.pow(
                Math.max(0, Math.sin(elapsed * 3.8)),
                18
            );

            const pulse = 1 + beat * 0.075;

            heart.scale.set(
                0.9 * pulse,
                0.9 * pulse,
                0.9 * pulse
            );

            heart.rotation.y = Math.sin(elapsed * 0.55) * 0.16;
            heart.rotation.x = Math.sin(elapsed * 0.4) * 0.08;

            orbiters.forEach((item, index) => {
                item.pivot.rotation.y += item.speed * 0.003;
                item.pivot.rotation.x = Math.sin(
                    elapsed * 0.35 + index
                ) * 0.15;

                item.object.rotation.y += 0.004;
            });

            rings.forEach((ring, index) => {
                ring.rotation.z += 0.0015 * (index + 1);
            });

            particles.rotation.y += 0.0005;
            particles.rotation.x += 0.0002;

            currentX += (pointerX - currentX) * 0.035;
            currentY += (pointerY - currentY) * 0.035;

            camera.position.x = currentX;
            camera.position.y = -currentY;
            camera.lookAt(0, 0, 0);
        }

        renderer.render(scene, camera);
    }

    const observer = new ResizeObserver(resize);
    observer.observe(host);

    resize();

    // Enable the scene only after the renderer is ready.
    host.classList.add("medivera-3d-ready");

    animate();

    window.addEventListener("pagehide", () => {
        cancelAnimationFrame(frameId);
        observer.disconnect();
        host.removeEventListener("pointermove", onPointerMove);
        renderer.dispose();
    }, { once: true });

})();