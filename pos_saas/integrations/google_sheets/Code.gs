const SPREADSHEET_ID = '1Y1NqQHZK96QaHzUFMlc-nzRPPxaPm-r3SXxU6N0NvBU';
const TAB_NAME = 'Contact Enquiries';

function doPost(e) {
  const reply = data => ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  let lock;
  try {
    const data = JSON.parse(e.postData.contents);
    const token = PropertiesService.getScriptProperties().getProperty('CONTACT_TOKEN');
    if (!token || data.token !== token) return reply({ok:false});
    const limits = {name:150, phone:32, email:254, business_type:40, message:5000};
    for (const key in limits) {
      if (typeof data[key] !== 'string' || !data[key].trim() || data[key].length > limits[key]) return reply({ok:false});
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
    return reply({ok:false});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}
