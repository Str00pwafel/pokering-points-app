// Theme Loader - Loads and applies theme from server
(async function loadTheme() {
    try {
        const response = await fetch('/theme');
        if (!response.ok) {
            console.warn('Failed to load theme, using defaults');
            return;
        }

        const theme = await response.json();

        // Apply CSS variables to root element
        const root = document.documentElement;
        for (const [key, value] of Object.entries(theme.colors)) {
            root.style.setProperty(`--${key}`, value);
        }

        // Update logo if it exists on the page
        const logo = document.querySelector('img.logo');
        if (logo && theme.logo) {
            logo.src = `images/${theme.logo}`;

            // Add theme-specific decorations
            if (theme.name === 'Christmas' && theme.decorations) {
                addChristmasDecorations(logo, theme.decorations);
            } else if (theme.name === 'Koningsdag' && theme.decorations) {
                addKoningsdagDecorations(logo, theme.decorations);
            }
        }

        console.log(`Theme loaded: ${theme.name}`);
    } catch (error) {
        console.error('Error loading theme:', error);
        // Silently fail - CSS will use default variable values
    }
})();

// Inlined Santa Hat SVG (avoids HTTP request)
const SANTA_HAT_SVG = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHZpZXdCb3g9IjAgMCA4MCA4MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KICA8ZyBpZD0ic2FudGEtaGF0Ij4KICAgIDxwYXRoIGQ9Ik0gMjUgNTAgUSAzMCAyMCwgNDUgMTUgUSA2MCAxOCwgNjUgNTAgWiIgZmlsbD0iI2M0MWUzYSIgc3Ryb2tlPSIjOGIwMDAwIiBzdHJva2Utd2lkdGg9IjEiLz4KICAgIDxlbGxpcHNlIGN4PSI0NSIgY3k9IjUwIiByeD0iMjIiIHJ5PSI1IiBmaWxsPSIjZmZmZmZmIi8+CiAgICA8Y2lyY2xlIGN4PSI0NSIgY3k9IjEzIiByPSI2IiBmaWxsPSIjZmZmZmZmIi8+CiAgICA8cGF0aCBkPSJNIDI1IDUwIFEgMzAgMjAsIDQ1IDE1IFEgNDAgMjAsIDM4IDUwIFoiIGZpbGw9IiNhMDEwMjAiIG9wYWNpdHk9IjAuNCIvPgogIDwvZz4KPC9zdmc+Cg==';

// Inlined Crown SVG for Koningsdag - hand-drawn crown (pre-encoded)
const CROWN_SVG = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAwIiBoZWlnaHQ9IjkwIiB2aWV3Qm94PSIwIDAgMTAwIDkwIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgogIDxnIGZpbGw9Im5vbmUiIHN0cm9rZT0iI0ZGRkZGRiIgc3Ryb2tlLXdpZHRoPSIzLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+CiAgICA8cGF0aCBkPSJNIDE4IDY1IEMgMTQgNTUsIDEyIDQ1LCAxNiAzNSIvPgogICAgPGNpcmNsZSBjeD0iMTYiIGN5PSIzMCIgcj0iNCIgZmlsbD0ibm9uZSIvPgogICAgPHBhdGggZD0iTSAxOCAyNiBDIDIyIDMyLCAyOCA0MiwgMzIgNDgiLz4KICAgIDxwYXRoIGQ9Ik0gMzIgNDggQyAzNiAzOCwgNDIgMjIsIDUwIDE0Ii8+CiAgICA8Y2lyY2xlIGN4PSI1MCIgY3k9IjEwIiByPSI0LjUiIGZpbGw9Im5vbmUiLz4KICAgIDxwYXRoIGQ9Ik0gNTAgMTQgQyA1OCAyMiwgNjQgMzgsIDY4IDQ4Ii8+CiAgICA8cGF0aCBkPSJNIDY4IDQ4IEMgNzIgNDIsIDc4IDMyLCA4MiAyNiIvPgogICAgPGNpcmNsZSBjeD0iODQiIGN5PSIzMCIgcj0iNCIgZmlsbD0ibm9uZSIvPgogICAgPHBhdGggZD0iTSA4OCAzNSBDIDg4IDQ1LCA4NiA1NSwgODIgNjUiLz4KICAgIDxlbGxpcHNlIGN4PSI1MCIgY3k9IjY4IiByeD0iMzYiIHJ5PSI4IiBzdHJva2Utd2lkdGg9IjQiLz4KICAgIDxlbGxpcHNlIGN4PSI1MCIgY3k9IjY4IiByeD0iMzYiIHJ5PSI4IiBzdHJva2Utd2lkdGg9IjEuNSIgc3Ryb2tlPSIjRkZGRkZGIi8+CiAgPC9nPgo8L3N2Zz4=';

function cleanupDecorations(logo) {
    // Remove logo container wrapper and unwrap logo if it exists
    const container = logo.parentElement;
    if (container && container.classList.contains('logo-container')) {
        const parent = container.parentElement;

        // Move logo back to original parent
        parent.insertBefore(logo, container);

        // Remove the container and all decorations
        container.remove();
    }
}

function addChristmasDecorations(logo, decorations) {
    // Clean up any existing decorations first
    cleanupDecorations(logo);

    // Wrap logo in a new container
    const container = document.createElement('div');
    container.className = 'logo-container';
    logo.parentNode.insertBefore(container, logo);
    container.appendChild(logo);

    // Add Santa hat overlay if enabled
    if (decorations.santaHat && decorations.santaHat.enabled) {
        const hatConfig = decorations.santaHat;
        const hatOverlay = document.createElement('img');
        hatOverlay.src = SANTA_HAT_SVG;
        hatOverlay.className = 'santa-hat-overlay';
        hatOverlay.alt = 'Santa Hat';

        // Apply config-based styles
        hatOverlay.style.width = `${hatConfig.size}px`;
        hatOverlay.style.height = `${hatConfig.size}px`;
        hatOverlay.style.top = `${hatConfig.top}px`;
        hatOverlay.style.marginLeft = `${hatConfig.marginLeft}px`;

        // Set CSS custom property for rotation to use in animation
        hatOverlay.style.setProperty('--hat-rotation', `${hatConfig.rotation}deg`);

        container.appendChild(hatOverlay);
    }

    // Add snowflakes if enabled
    if (decorations.snowflakes && decorations.snowflakes.enabled) {
        addSnowflakes(container, decorations.snowflakes.count);
    }
}

function addSnowflakes(container, count = 8) {
    const snowflakeContainer = document.createElement('div');
    snowflakeContainer.className = 'snowflakes';
    snowflakeContainer.setAttribute('aria-hidden', 'true');

    // Generate evenly distributed positions based on count
    const positions = [];
    const step = 100 / (count + 1);
    for (let i = 1; i <= count; i++) {
        positions.push(step * i);
    }

    for (let i = 0; i < positions.length; i++) {
        const snowflake = document.createElement('div');
        snowflake.className = 'snowflake';
        snowflake.textContent = '❄';
        // Use calculated positions with slight random offset for natural look
        snowflake.style.left = `${positions[i] + (Math.random() * 8 - 4)}%`;
        snowflake.style.animationDelay = `${Math.random() * 3}s`;
        snowflake.style.animationDuration = `${3 + Math.random() * 2}s`;
        snowflakeContainer.appendChild(snowflake);
    }

    container.appendChild(snowflakeContainer);
}

function addKoningsdagDecorations(logo, decorations) {
    // Clean up any existing decorations first
    cleanupDecorations(logo);

    // Wrap logo in a new container
    const container = document.createElement('div');
    container.className = 'logo-container';
    logo.parentNode.insertBefore(container, logo);
    container.appendChild(logo);

    // Add crown overlay if enabled
    if (decorations.crown && decorations.crown.enabled) {
        const crownConfig = decorations.crown;
        const crownOverlay = document.createElement('img');
        crownOverlay.src = CROWN_SVG;
        crownOverlay.className = 'crown-overlay';
        crownOverlay.alt = 'Crown';

        // Apply config-based styles
        crownOverlay.style.width = `${crownConfig.size}px`;
        crownOverlay.style.height = `${crownConfig.size}px`;
        crownOverlay.style.top = `${crownConfig.top}px`;
        crownOverlay.style.marginLeft = `${crownConfig.marginLeft}px`;

        // Set CSS custom property for rotation to use in animation
        crownOverlay.style.setProperty('--crown-rotation', `${crownConfig.rotation}deg`);

        container.appendChild(crownOverlay);
    }

    // Add Dutch flags if enabled
    if (decorations.dutchFlags && decorations.dutchFlags.enabled) {
        addDutchFlags(container, decorations.dutchFlags.count);
    }
}

function addDutchFlags(container, count = 6) {
    const flagContainer = document.createElement('div');
    flagContainer.className = 'dutch-flags';
    flagContainer.setAttribute('aria-hidden', 'true');

    // Generate evenly distributed positions
    const positions = [];
    const step = 100 / (count + 1);
    for (let i = 1; i <= count; i++) {
        positions.push(step * i);
    }

    for (let i = 0; i < positions.length; i++) {
        const flag = document.createElement('div');
        flag.className = 'dutch-flag';

        // Create inline Dutch flag using CSS
        flag.style.position = 'absolute';
        flag.style.width = '30px';
        flag.style.height = '20px';
        flag.style.background = 'linear-gradient(to bottom, #AE1C28 33.33%, #ffffff 33.33% 66.66%, #21468B 66.66%)';
        flag.style.border = '1px solid #ccc';
        flag.style.left = `${positions[i] + (Math.random() * 8 - 4)}%`;
        flag.style.animationDelay = `${Math.random() * 3}s`;
        flag.style.animationDuration = `${4 + Math.random() * 2}s`;
        flagContainer.appendChild(flag);
    }

    container.appendChild(flagContainer);
}
