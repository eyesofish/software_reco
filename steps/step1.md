You are assisting with a React project refactor.

CRITICAL WORKFLOW RULES:

1. You must work in SMALL incremental steps.
2. Only implement ONE feature per step.
3. After completing a step, STOP and wait.
4. Do NOT continue automatically.
5. Do NOT modify unrelated files.
6. Show all modified files and full code.
7. Clearly label the step as completed.

I will:

- run npm start
- test the changes
- commit the changes

Then I will tell you to proceed to the next step.

Do NOT skip ahead.
Do NOT batch multiple features.

Reply: "Ready for Step 1"

## STEP 1: Implement theme switching only.

### Requirements:
- Modify Config page
- Add Theme selector with options: Dark and Light
- Store selection in localStorage: localStorage.setItem("theme", value)
- Apply theme using: document.documentElement.setAttribute("data-theme", value)

### Detailed Instructions:
1. Open the Config page file (likely [src/pages/config/index.tsx](file:///d:/Github/software_reco/ollama_springboot/ollama-gui-reactjs/src/pages/config/index.tsx))
2. Create a dropdown or selector component for theme selection
3. Implement state management for the selected theme
4. Add useEffect to load the theme from localStorage on component mount
5. Add onChange handler to update localStorage and apply the theme to document.documentElement
6. Make sure to apply the theme immediately when selected
7. Test both themes to ensure they work properly

Do NOT implement anything else.

After completing, STOP and wait for confirmation.

Show modified files.

## STEP 2: Implement animation enable/disable toggle.

### Requirements:
- Add checkbox in Config page: "Enable splash animation"
- Store in localStorage: enableSplash: true/false
- Modify SplashLifecycle to respect this setting

### Detailed Instructions:
1. Locate the Config page component
2. Add a checkbox input with label "Enable splash animation"
3. Implement state handling for the checkbox
4. Load the setting from localStorage on mount
5. Update localStorage when the checkbox value changes
6. Find the SplashLifecycle component or splash screen logic
7. Modify the splash screen to check this setting before showing animation
8. Ensure the setting is respected throughout the app

Do NOT modify theme system.

STOP after completion.

## STEP 3: Add animation speed slider.

### Requirements:
- Slider range: 0.1 to 2.0
- Store in localStorage: starSpeed
- SplashScreen reads this value

### Detailed Instructions:
1. Locate the Config page component
2. Add a slider/range input with min="0.1", max="2.0", step="0.1"
3. Set up state management for the slider value
4. Load the saved speed value from localStorage on mount
5. Update localStorage when slider value changes
6. Find the SplashScreen component or animation logic
7. Modify the animation to use the stored speed value
8. Test various speed values to ensure they work correctly

STOP after completion.

## STEP 4: Implement language selection feature.

### Requirements:
- Add language selector in Config page
- Support English and Chinese languages
- Store selection in localStorage: language
- Update UI texts based on selected language

### Detailed Instructions:
1. Add language selection dropdown in Config page
2. Implement state for language selection
3. Store language preference in localStorage
4. Create basic i18n structure for the app
5. Apply language changes dynamically to affected UI elements
6. Test that language changes take effect immediately

STOP after completion.

## STEP 5: Add font size adjustment controls.

### Requirements:
- Slider control for font size (range: 14px to 20px)
- Store in localStorage: fontSize
- Apply to entire app via CSS custom properties

### Detailed Instructions:
1. Add font size slider to Config page
2. Implement state handling for the slider
3. Store font size preference in localStorage
4. Apply font size via CSS custom properties
5. Test that all text elements respond appropriately to font size changes
6. Ensure accessibility considerations are maintained

STOP after completion.