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
