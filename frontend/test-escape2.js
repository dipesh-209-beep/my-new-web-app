const escapeHtml = (value) => {
  return value
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">")
    .replace(/"/g, "\"")
    .replace(/'/g, "'");
};

const test = 'New Road <script>alert(1)</script>';
const result = escapeHtml(test);
console.log('Result contains <:', result.includes('<'));
console.log('Result contains <script>:', result.includes('<script>'));
console.log('Result:', JSON.stringify(result));
