export const metadata = { title: 'CowAgent Dashboard' };

export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN">
      <body style={{ fontFamily: 'Arial', margin: 20 }}>{children}</body>
    </html>
  );
}
