import typography from '@tailwindcss/typography'

export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        tweetBlue: 'rgb(var(--color-tweetBlue) / <alpha-value>)',
        bgMain: 'rgb(var(--color-bgMain) / <alpha-value>)',
        bgColumn: 'rgb(var(--color-bgColumn) / <alpha-value>)',
        bgCard: 'rgb(var(--color-bgCard) / <alpha-value>)',
        bgCardAlt: 'rgb(var(--color-bgCardAlt) / <alpha-value>)',
        textSubtle: 'rgb(var(--color-textSubtle) / <alpha-value>)',
        successGreen: 'rgb(var(--color-successGreen) / <alpha-value>)',
        warningOrange: 'rgb(var(--color-warningOrange) / <alpha-value>)',
        errorRed: 'rgb(var(--color-errorRed) / <alpha-value>)',
        textPrimary: 'rgb(var(--color-textPrimary) / <alpha-value>)',
        textHeading: 'rgb(var(--color-textHeading) / <alpha-value>)',
        textSecondary: 'rgb(var(--color-textSecondary) / <alpha-value>)',
      },
    },
  },
  plugins: [typography],
}
