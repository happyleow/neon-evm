use std::cell::RefMut;
use arrayref::{array_mut_ref, array_ref, array_refs, mut_array_refs};
use solana_program::program_error::ProgramError;
use solana_program::pubkey::Pubkey;
use crate::hamt::Hamt;
use super::{ Packable, AccountExtension };

/// Ethereum contract data account
#[derive(Debug)]
pub struct Data {
    /// Solana account with ethereum account data associated with this code data
    pub owner: Pubkey,
    /// Contract code size
    pub code_size: u32,
}

#[derive(Debug)]
pub struct Extension<'a> {
    pub code: RefMut<'a, [u8]>,
    pub valids: RefMut<'a, [u8]>,
    pub storage: Hamt<'a>
}

impl<'a> AccountExtension<'a, Data> for Extension<'a> {
    fn unpack(data: &Data, remaining: RefMut<'a, [u8]>) -> Result<Self, ProgramError> {
        let code_size = data.code_size as usize;
        let valids_size = (code_size / 8) + 1;

        let (code, rest) = RefMut::map_split(remaining, |r| r.split_at_mut(code_size));
        let (valids, storage) = RefMut::map_split(rest, |r| r.split_at_mut(valids_size));

        Ok(Self { code, valids, storage: Hamt::new(storage)? })
    }
}

impl Packable for Data {
    /// Contract struct tag
    const TAG: u8 = super::TAG_CONTRACT;
    /// Contract struct serialized size
    const SIZE: usize = 32 + 4;

    /// Deserialize `Contract` struct from input data
    #[must_use]
    fn unpack(input: &[u8]) -> Self {
        #[allow(clippy::use_self)]
        let data = array_ref![input, 0, Data::SIZE];
        let (owner, code_size) = array_refs![data, 32, 4];

        Self {
            owner: Pubkey::new_from_array(*owner),
            code_size: u32::from_le_bytes(*code_size),
        }
    }

    /// Serialize `Contract` struct into given destination
    fn pack(&self, dst: &mut [u8]) {
        #[allow(clippy::use_self)]
        let data = array_mut_ref![dst, 0, Data::SIZE];
        let (owner_dst, code_size_dst) = mut_array_refs![data, 32, 4];
        owner_dst.copy_from_slice(self.owner.as_ref());
        *code_size_dst = self.code_size.to_le_bytes();
    }
}
