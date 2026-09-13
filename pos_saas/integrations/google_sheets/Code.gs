const SPREADSHEET_ID = '1Y1NqQHZK96QaHzUFMlc-nzRPPxaPm-r3SXxU6N0NvBU';
const TAB_NAME = 'Contact Enquiries';

function doGet() {
  return ContentService.createTextOutput(JSON.stringify({
    ok: true, message: 'Contact form endpoint is running.'
  })).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  const reply = data => ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  let lock;
  try {
    let data;
    try {
      data = JSON.parse(e.postData.contents);
    } catch (_) {
      return reply({ok:false, code:'INVALID_JSON'});
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return reply({ok:false, code:'INVALID_JSON'});
    }
    const token = PropertiesService.getScriptProperties().getProperty('CONTACT_TOKEN');
    if (!token) return reply({ok:false, code:'SCRIPT_TOKEN_MISSING'});
    if (data.token !== token) return reply({ok:false, code:'TOKEN_MISMATCH'});
    const limits = {name:150, phone:32, email:254, business_type:40, message:5000};
    for (const key in limits) {
      if (typeof data[key] !== 'string' || !data[key].trim() || data[key].length > limits[key]) return reply({ok:false, code:'INVALID_FIELD', field:key});
    }
    lock = LockService.getScriptLock();
    lock.waitLock(20000);
    const book = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheet = book.getSheetByName(TAB_NAME) || book.insertSheet(TAB_NAME);
    if (!sheet.getLastRow()) sheet.appendRow(['Received At', 'Name', 'Phone Number', 'Email', 'Business Type', 'Message', 'WhatsApp Contact']);
    // Store visitor-provided values as literal text, never spreadsheet formulas.
    const literal = value => "'" + String(value);
    sheet.appendRow([new Date(), literal(data.name), literal(data.phone), literal(data.email),
      literal(data.business_type), literal(data.message), data.whatsapp_opt_in === true ? 'Yes' : 'No']);
    return reply({ok:true});
  } catch (error) {
    console.error(error);
    return reply({ok:false, code:'SHEET_WRITE_FAILED'});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}
