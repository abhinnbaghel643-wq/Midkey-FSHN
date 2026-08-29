import streamlit as st
import requests

BACKEND_URL = "http://localhost:3000"

st.set_page_config(page_title="FSHN", page_icon="🪞")

if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'body_scan' not in st.session_state:
    st.session_state.body_scan = None

st.title("🪞 FSHN")

st.header("1. Scan Your Body")
name = st.text_input("Your name")
body_photo = st.file_uploader(
    "Upload 1-2 photo of yourself",
    type=['jpg', 'jpeg', 'png'],
    accept_multiple_files=True,
    key="body_uploader"
)

if st.button("Scan My Style"):
    if not name:
        st.warning("Please enter your name first.")
    elif not body_photo:
        st.warning("Please upload at least one photo first.")
    else:
        with st.spinner("Scanning..."):
            try:
                files = [("photo", (photo.name, photo.getvalue(), photo.type)) for photo in body_photo]
                data = {"name": name}
                response = requests.post(f"{BACKEND_URL}/api/scan-body", files=files, data=data)
                result = response.json()
                if 'error' in result:
                    st.error(result['error'])
                else:
                    st.session_state.body_scan = result
                    st.session_state.user_id = result.get('user_id')
            except requests.exceptions.ConnectionError:
                st.error("Couldn't reach the backend. Is your teammate's Flask server running?")
            except Exception as e:
                st.error(f"Something went wrong: {e}")

if st.session_state.body_scan:
    data = st.session_state.body_scan
    st.success(f"Scan complete! (User ID: {st.session_state.user_id})")
    st.write(f"**Shape:** {data.get('shape')}")
    st.write(f"**Skin tone:** {data.get('skintone')}")
    st.write(f"**Proportions:** {data.get('proportions')}")

st.divider()

st.header("2. Upload Closet Items")
item_photo = st.file_uploader(
    "Upload a photo of one clothing item",
    type=['jpg', 'jpeg', 'png'],
    key="item_uploader"
)

if st.button("Add Item"):
    if not st.session_state.user_id:
        st.warning("Scan your body first (Section 1) to create a user.")
    elif not item_photo:
        st.warning("Please upload a photo first.")
    else:
        with st.spinner("Identifying item..."):
            try:
                files = {"photo": (item_photo.name, item_photo.getvalue(), item_photo.type)}
                data = {"user_id": st.session_state.user_id}
                response = requests.post(f"{BACKEND_URL}/api/scan-item", files=files, data=data)
                result = response.json()
                if 'error' in result:
                    st.error(result['error'])
                else:
                    st.success(f"Added: {result.get('type')}")
            except requests.exceptions.ConnectionError:
                st.error("Couldn't reach the backend. Is your teammate's Flask server running?")
            except Exception as e:
                st.error(f"Something went wrong: {e}")

if st.session_state.user_id:
    try:
        wardrobe_response = requests.get(f"{BACKEND_URL}/api/wardrobe/{st.session_state.user_id}")
        wardrobe_data = wardrobe_response.json()
        items = wardrobe_data.get('items', [])
        if items:
            st.write("**Your closet so far:**")
            for item in items:
                col1, col2 = st.columns([1, 3])
                with col1:
                    if item.get('image_path'):
                        st.image(f"{BACKEND_URL}/uploads/{item['image_path']}", width=80)
                with col2:
                    st.write(f"{item.get('color')} {item.get('cut')} {item.get('type')} ({item.get('fabric')})")
    except requests.exceptions.ConnectionError:
        st.info("Backend not reachable yet.")
    except Exception as e:
        st.error(f"Something went wrong loading your wardrobe: {e}")

st.divider()

st.header("3. Style My Closet")

if st.button("Generate Outfits"):
    if not st.session_state.user_id:
        st.warning("Scan your body first (Section 1).")
    else:
        with st.spinner("Styling..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/api/style-closet",
                    json={"user_id": st.session_state.user_id}
                )
                result = response.json()
                if 'error' in result:
                    st.error(result['error'])
                else:
                    for outfit in result.get('outfits', []):
                        st.subheader(outfit.get('name'))
                        outfit_items = outfit.get('items', [])
                        if outfit_items:
                            cols = st.columns(len(outfit_items))
                            for col, item in zip(cols, outfit_items):
                                with col:
                                    if item.get('image_path'):
                                        st.image(f"{BACKEND_URL}/uploads/{item['image_path']}", width=120)
                                    st.caption(f"{item.get('color')} {item.get('cut')} {item.get('type')}")
                        st.write(f"*{outfit.get('reason')}*")
            except requests.exceptions.ConnectionError:
                st.error("Couldn't reach the backend. Is your teammate's Flask server running?")
            except Exception as e:
                st.error(f"Something went wrong: {e}")