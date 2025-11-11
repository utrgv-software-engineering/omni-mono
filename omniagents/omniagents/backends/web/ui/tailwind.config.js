/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        tweetBlue: '#1DA1F2',
        bgMain: '#15202B',
        bgColumn: '#192734',
        bgCard: '#1E2732',
        bgCardAlt: '#253341',
        textSubtle: '#8899A6',
        divider: '#38444D',
        successGreen: '#17BF63',
        warningOrange: '#FFA534',
        errorRed: '#E0245E',
      },
    },
  },
  plugins: [],
}
