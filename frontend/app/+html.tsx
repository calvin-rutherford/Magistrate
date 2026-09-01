import React from 'react';

export default function Html({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <title>Magistrate</title>
      </head>
      <body>{children}</body>
    </html>
  );
}
