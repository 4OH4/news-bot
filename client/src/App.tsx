/** Placeholder screen: real screens arrive with #180 to #184. */
export function App() {
  return (
    <main>
      <h1>Newsfeed</h1>
      <p id="status">The app is running.</p>
      <p id="version" data-version={__APP_VERSION__}>
        Build {__APP_VERSION__}
      </p>
    </main>
  );
}
