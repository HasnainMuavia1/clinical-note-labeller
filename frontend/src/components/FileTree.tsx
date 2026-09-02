export default function FileTree({ paths }: { paths: string[] }) {
  return (
    <section className="tree">
      <h3>Output ({paths.length} files)</h3>
      <ul>
        {paths.map((path) => (
          <li key={path}>
            <code>{path}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
