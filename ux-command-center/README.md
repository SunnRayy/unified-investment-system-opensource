<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/1XIpUUyPnK5ywns4dJRP2tqufAOlB4TfS

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in `.env.local` to your Gemini API key
3. Run the app:
   `npm run dev`

## Visualization Style (V5.10.1)

- **Default mode:** Day/Light (day-first design system)
- **Alternate mode:** Night/Dark (toggle from sidebar shell)
- **Theme persistence:** Stored in local storage key `uis-theme`
- **Core light tokens:**
  - Background: `#F9FAFB`
  - Card surface: `#FFFFFF`
  - Primary: `#3b82f6`

## Quality Checks

Run before publishing UI changes:

1. `npm run test`
2. `npm run build`
