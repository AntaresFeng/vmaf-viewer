(function (global) {
  const DEFAULT_STATUS_MESSAGE = "Select VMAF log files to compare.";

  function firstText(value) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (typeof item !== "string") {
        continue;
      }
      const text = item.trim();
      if (text) {
        return text;
      }
    }
    return "";
  }

  function formatLoadedMessage(summary) {
    const rows = Array.isArray(summary) ? summary : [];
    const fileCount = rows.length;
    const fileWord = fileCount === 1 ? "file" : "files";
    return `Loaded ${fileCount} ${fileWord}.`;
  }

  function pickMessageState(input = {}) {
    const { error = null, warnings = [], status = null, success = null } = input;
    const errorText = firstText(error);
    if (errorText) {
      return { text: errorText, type: "error" };
    }

    const warningText = firstText(warnings);
    if (warningText) {
      return { text: warningText, type: "warning" };
    }

    const statusText = firstText(status);
    if (statusText) {
      return { text: statusText, type: "status" };
    }

    const successText = firstText(success);
    if (successText) {
      return { text: successText, type: "status" };
    }

    return { text: DEFAULT_STATUS_MESSAGE, type: "status" };
  }

  global.VmafMessageState = {
    DEFAULT_STATUS_MESSAGE,
    formatLoadedMessage,
    pickMessageState,
  };
})(globalThis);
