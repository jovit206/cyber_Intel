window.AEGIS_API_BASE_URL = window.AEGIS_API_BASE_URL || (
  window.location.protocol === 'file:'
    ? 'http://127.0.0.1:8000'
    : window.location.port === '3000'
      ? `${window.location.protocol}//${window.location.hostname}:8000`
      : ''
);
