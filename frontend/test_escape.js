const escapeHtml = (value) => {
  return value
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">")
    .replace(/"/g, "\"")
    .replace(/'/g, "&apos;");
};

const test = 'New Road <script>alert(1)</script>';
const result = escapeHtml(test);
console.log('Result JSON:', JSON.stringify(result));
console.log('Contains <:', result.includes('<'));
console.log('Contains >:', result.includes('>'));
console.log('Contains ":', result.includes('"'));