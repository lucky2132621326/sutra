export type Locale = 'en' | 'hi' | 'te'

export function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'hi' || value === 'te'
}

export function storedLocale(): Locale {
  if (typeof window === 'undefined') return 'en'
  const value = window.localStorage.getItem('sutra-locale')
  return isLocale(value) ? value : 'en'
}

export const LANGUAGES: { value: Locale; label: string; speech: string }[] = [
  { value: 'en', label: 'English', speech: 'en-IN' },
  { value: 'hi', label: 'हिन्दी', speech: 'hi-IN' },
  { value: 'te', label: 'తెలుగు', speech: 'te-IN' },
]

const COPY = {
  en: {
    replay: 'Replay', live: 'Live', backendUp: 'backend up', backendDown: 'backend down', inbox: 'Inbox',
    newChat: 'New chat', inspectRun: 'Inspect run', closeInspection: 'Close inspection', dark: 'Dark', light: 'Light',
    playRun: 'Play run', playShowcase: 'Play full showcase', welcomeTitle: 'Ask the campus anything.',
    welcomeBody: "Five specialist agents plan together, check each other's work against real records, and stop for your approval before anything is written.",
    tryOne: 'Try one', heroRun: 'The hero run', attendanceRule: 'Attendance rule', eligibilityOnly: 'Eligibility only',
    heroPrompt: "I'm a third-year CSE student. Am I eligible for the Google internship? If yes, register me for the placement workshop, add it to my calendar, and remind me an hour before.",
    attendancePrompt: 'What attendance do I need to sit for exams, and am I currently short in anything?',
    eligibilityPrompt: 'Am I eligible for the Google internship?', placeholder: 'Ask anything…',
    replayPlaceholder: 'Ask anything — or press Play to replay a recorded run…', send: 'Send', stop: 'Stop',
    enterHint: 'Enter to send · Shift+Enter for a new line', runProgress: 'Run in progress — Stop keeps your draft',
    replayHint: 'Replay mode — sending switches to live',
    backendUnavailable: 'Backend is not reachable. Switch to Replay to show a recorded run.',
    voiceStart: 'Speak your request', voiceStop: 'Stop listening', listening: 'Listening… speak naturally',
    voiceCaptured: 'Voice captured — review it, then send', voiceNoSpeech: 'I did not hear anything. Try again.',
    voiceError: 'Voice input failed. Please try again.',
    voiceUnsupported: 'Voice input is not supported in this browser. Use Chrome or Edge.',
    voiceDenied: 'Microphone access was denied. Allow it in browser settings and try again.', language: 'Language',
  },
  hi: {
    replay: 'रिकॉर्डेड', live: 'लाइव', backendUp: 'बैकएंड चालू', backendDown: 'बैकएंड बंद', inbox: 'इनबॉक्स',
    newChat: 'नई चैट', inspectRun: 'रन देखें', closeInspection: 'निरीक्षण बंद करें', dark: 'डार्क', light: 'लाइट',
    playRun: 'रन चलाएँ', playShowcase: 'पूरा प्रदर्शन चलाएँ', welcomeTitle: 'कैंपस से कुछ भी पूछें।',
    welcomeBody: 'पाँच विशेषज्ञ एजेंट मिलकर योजना बनाते हैं, वास्तविक रिकॉर्ड जाँचते हैं और कोई बदलाव करने से पहले आपकी अनुमति लेते हैं।',
    tryOne: 'इसे आज़माएँ', heroRun: 'मुख्य डेमो', attendanceRule: 'उपस्थिति नियम', eligibilityOnly: 'केवल पात्रता',
    heroPrompt: 'मैं तीसरे वर्ष का CSE छात्र हूँ। क्या मैं Google इंटर्नशिप के लिए पात्र हूँ? यदि हाँ, मुझे प्लेसमेंट वर्कशॉप में पंजीकृत करें, कैलेंडर में जोड़ें और एक घंटे पहले याद दिलाएँ।',
    attendancePrompt: 'परीक्षा देने के लिए कितनी उपस्थिति चाहिए और किन विषयों में मेरी उपस्थिति कम है?',
    eligibilityPrompt: 'क्या मैं Google इंटर्नशिप के लिए पात्र हूँ?', placeholder: 'कुछ भी पूछें…',
    replayPlaceholder: 'कुछ भी पूछें — या रिकॉर्डेड रन चलाएँ…', send: 'भेजें', stop: 'रोकें',
    enterHint: 'भेजने के लिए Enter · नई पंक्ति के लिए Shift+Enter', runProgress: 'रन चल रहा है — रोकने पर आपका ड्राफ्ट सुरक्षित रहेगा',
    replayHint: 'रिकॉर्डेड मोड — भेजते ही लाइव होगा', backendUnavailable: 'बैकएंड उपलब्ध नहीं है। रिकॉर्डेड रन दिखाने के लिए Replay चुनें।',
    voiceStart: 'अपना अनुरोध बोलें', voiceStop: 'सुनना रोकें', listening: 'सुन रहा हूँ… स्वाभाविक रूप से बोलें',
    voiceCaptured: 'आवाज़ दर्ज हो गई — जाँचकर भेजें', voiceNoSpeech: 'कोई आवाज़ सुनाई नहीं दी। फिर से प्रयास करें।',
    voiceError: 'वॉइस इनपुट विफल रहा। फिर से प्रयास करें।',
    voiceUnsupported: 'इस ब्राउज़र में वॉइस इनपुट उपलब्ध नहीं है। Chrome या Edge इस्तेमाल करें।',
    voiceDenied: 'माइक्रोफ़ोन की अनुमति नहीं मिली। ब्राउज़र सेटिंग में अनुमति देकर दोबारा प्रयास करें।', language: 'भाषा',
  },
  te: {
    replay: 'రికార్డెడ్', live: 'లైవ్', backendUp: 'బ్యాకెండ్ ఆన్', backendDown: 'బ్యాకెండ్ ఆఫ్', inbox: 'ఇన్‌బాక్స్',
    newChat: 'కొత్త చాట్', inspectRun: 'రన్ చూడండి', closeInspection: 'పరిశీలన మూసివేయండి', dark: 'డార్క్', light: 'లైట్',
    playRun: 'రన్ ప్లే చేయండి', playShowcase: 'పూర్తి ప్రదర్శన ప్లే చేయండి', welcomeTitle: 'క్యాంపస్ గురించి ఏదైనా అడగండి.',
    welcomeBody: 'ఐదు ప్రత్యేక ఏజెంట్లు కలిసి ప్రణాళిక చేస్తారు, నిజమైన రికార్డులను తనిఖీ చేస్తారు మరియు ఏ మార్పుకైనా ముందు మీ అనుమతి తీసుకుంటారు.',
    tryOne: 'ఒకటి ప్రయత్నించండి', heroRun: 'ప్రధాన డెమో', attendanceRule: 'హాజరు నియమం', eligibilityOnly: 'అర్హత మాత్రమే',
    heroPrompt: 'నేను మూడో సంవత్సరం CSE విద్యార్థిని. Google ఇంటర్న్‌షిప్‌కు అర్హుడినా? అర్హత ఉంటే ప్లేస్‌మెంట్ వర్క్‌షాప్‌కు నమోదు చేసి, క్యాలెండర్‌లో జోడించి, గంట ముందు గుర్తు చేయండి.',
    attendancePrompt: 'పరీక్షలకు హాజరవ్వడానికి ఎంత హాజరు కావాలి, ఏ కోర్సుల్లో నా హాజరు తక్కువగా ఉంది?',
    eligibilityPrompt: 'నేను Google ఇంటర్న్‌షిప్‌కు అర్హుడినా?', placeholder: 'ఏదైనా అడగండి…',
    replayPlaceholder: 'ఏదైనా అడగండి — లేదా రికార్డెడ్ రన్ ప్లే చేయండి…', send: 'పంపండి', stop: 'ఆపండి',
    enterHint: 'పంపడానికి Enter · కొత్త లైన్‌కు Shift+Enter', runProgress: 'రన్ కొనసాగుతోంది — ఆపినా మీ డ్రాఫ్ట్ ఉంటుంది',
    replayHint: 'రికార్డెడ్ మోడ్ — పంపితే లైవ్‌కు మారుతుంది', backendUnavailable: 'బ్యాకెండ్ అందుబాటులో లేదు. రికార్డెడ్ రన్ కోసం Replay ఎంచుకోండి.',
    voiceStart: 'మీ అభ్యర్థన చెప్పండి', voiceStop: 'వినడం ఆపండి', listening: 'వింటున్నాను… సహజంగా మాట్లాడండి',
    voiceCaptured: 'వాయిస్ నమోదైంది — తనిఖీ చేసి పంపండి', voiceNoSpeech: 'ఏమీ వినిపించలేదు. మళ్లీ ప్రయత్నించండి.',
    voiceError: 'వాయిస్ ఇన్‌పుట్ విఫలమైంది. మళ్లీ ప్రయత్నించండి.',
    voiceUnsupported: 'ఈ బ్రౌజర్‌లో వాయిస్ ఇన్‌పుట్ లేదు. Chrome లేదా Edge ఉపయోగించండి.',
    voiceDenied: 'మైక్రోఫోన్ అనుమతి లేదు. బ్రౌజర్ సెట్టింగ్స్‌లో అనుమతించి మళ్లీ ప్రయత్నించండి.', language: 'భాష',
  },
} as const

export type CopyKey = keyof typeof COPY.en
export function copy(locale: Locale, key: CopyKey): string { return COPY[locale][key] }
export function speechLocale(locale: Locale): string { return LANGUAGES.find((item) => item.value === locale)?.speech ?? 'en-IN' }
